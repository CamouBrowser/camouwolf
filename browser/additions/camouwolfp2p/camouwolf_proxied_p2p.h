// Copyright 2026 The Camouwolf Authors.
// Use of this source code is governed by a BSD-style license.
//
// WebRTC in a proxied context, as a machine at the proxy's exit has it.
//
// Stock Chromium sends WebRTC's UDP around a proxy, from this machine's own
// interfaces, so STUN reports this machine's public address. Here a context
// whose proxy is not DIRECT gets its identity's network (`webrtc:ipv4`,
// `webrtc:ipv6`, which the browser hands over with the socket manager)
// instead of this machine's, and every UDP datagram that leaves
// the host goes through a SOCKS5 UDP ASSOCIATE relay (RFC 1928 section 7) on
// that proxy, or nowhere: a proxy that carries no UDP looks like a firewall
// that drops it. STUN and TURN servers given by name are resolved by the
// proxy, never by this machine. See docs/PATCHES.md, 0031.

#ifndef SERVICES_NETWORK_P2P_CAMOUWOLF_PROXIED_P2P_H_
#define SERVICES_NETWORK_P2P_CAMOUWOLF_PROXIED_P2P_H_

#include <stddef.h>
#include <stdint.h>

#include <deque>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include "base/functional/callback.h"
#include "base/memory/raw_ptr.h"
#include "base/memory/scoped_refptr.h"
#include "net/base/address_family.h"
#include "net/base/io_buffer.h"
#include "net/base/ip_address.h"
#include "net/base/ip_endpoint.h"
#include "net/base/network_anonymization_key.h"
#include "net/base/network_interfaces.h"
#include "net/dns/host_resolver.h"
#include "net/proxy_resolution/proxy_info.h"
#include "net/socket/datagram_server_socket.h"
#include "net/socket/socks5_client_socket.h"
#include "net/socket/udp_server_socket.h"
#include "net/traffic_annotation/network_traffic_annotation.h"
#include "services/network/p2p/socket_udp.h"

namespace net {
class ClientSocketFactory;
class ProxyResolutionRequest;
class StreamSocket;
class URLRequestContext;
}  // namespace net

namespace network {

// Where one context's WebRTC leaves this machine. Found once, from the
// context's own proxy settings, so a per-context proxy is honoured.
class ProxiedP2P {
 public:
  ProxiedP2P(net::URLRequestContext* context,
             const net::NetworkAnonymizationKey& network_anonymization_key,
             std::vector<net::IPAddress> identity_addresses);
  ProxiedP2P(const ProxiedP2P&) = delete;
  ProxiedP2P& operator=(const ProxiedP2P&) = delete;
  ~ProxiedP2P();

  bool known() const { return route_ != Route::kUnknown; }
  // Runs `then` once known() is true.
  void Wait(base::OnceClosure then);
  bool proxied() const { return route_ == Route::kProxied; }

  // The interfaces and default addresses WebRTC sees behind the proxy: the
  // identity's one adapter, or none when the identity has no address.
  net::NetworkInterfaceList Network() const;
  net::IPAddress DefaultAddress(int family) const;
  // A STUN or TURN server's name, behind the proxy.
  static net::IPAddressList Resolve(const std::string& host_name,
                                    std::optional<net::AddressFamily> family);
  // Makes the UDP sockets of a proxied context.
  P2PSocketUdp::DatagramServerSocketFactory UdpSocketFactory(
      const net::NetworkTrafficAnnotationTag& traffic_annotation) const;

 private:
  enum class Route { kUnknown, kDirect, kProxied };

  void OnProxyResolved(int result);
  void OnProxyHostResolved(int result);
  void Done(Route route);
  std::optional<net::IPAddress> IdentityAddress(int family) const;

  const raw_ptr<net::URLRequestContext> context_;
  const net::NetworkAnonymizationKey network_anonymization_key_;
  const std::vector<net::IPAddress> identity_addresses_;
  Route route_ = Route::kUnknown;
  // The SOCKS5 proxy, when the context's proxy is one: the only kind that
  // carries UDP.
  std::optional<net::IPEndPoint> socks5_;
  std::optional<net::Socks5Credentials> credentials_;
  net::ProxyInfo proxy_info_;
  std::unique_ptr<net::ProxyResolutionRequest> proxy_request_;
  std::unique_ptr<net::HostResolver::ResolveHostRequest> host_request_;
  std::vector<base::OnceClosure> waiting_;
};

// A UDP socket of a proxied context. It reports the identity's address with
// the port it holds here, and sends through the SOCKS5 relay, or drops what it
// would send off this host when there is none.
class ProxiedUdpSocket final : public net::DatagramServerSocket {
 public:
  ProxiedUdpSocket(std::optional<net::IPEndPoint> socks5,
                   std::optional<net::Socks5Credentials> credentials,
                   net::ClientSocketFactory* client_socket_factory,
                   const net::NetworkTrafficAnnotationTag& traffic_annotation,
                   net::NetLog* net_log);
  ProxiedUdpSocket(const ProxiedUdpSocket&) = delete;
  ProxiedUdpSocket& operator=(const ProxiedUdpSocket&) = delete;
  ~ProxiedUdpSocket() override;

  // net::DatagramServerSocket:
  int Listen(const net::IPEndPoint& address) override;
  int RecvFrom(net::IOBuffer* buf,
               int buf_len,
               net::IPEndPoint* address,
               net::CompletionOnceCallback callback) override;
  int SendTo(net::IOBuffer* buf,
             int buf_len,
             const net::IPEndPoint& address,
             net::CompletionOnceCallback callback) override;
  int GetLocalAddress(net::IPEndPoint* address) const override;
  void Close() override;

  int SetReceiveBufferSize(int32_t size) override;
  int SetSendBufferSize(int32_t size) override;
  void AllowAddressReuse() override;
  void AllowBroadcast() override;
  void AllowAddressSharingForMulticast() override;
  int JoinGroup(const net::IPAddress& group_address) const override;
  int LeaveGroup(const net::IPAddress& group_address) const override;
  int SetMulticastInterface(uint32_t interface_index) override;
  int SetMulticastTimeToLive(int time_to_live) override;
  int SetMulticastLoopbackMode(bool loopback) override;
  int SetDiffServCodePoint(net::DiffServCodePoint dscp) override;
  void DetachFromThread() override;
  int GetPeerAddress(net::IPEndPoint* address) const override;
  void UseNonBlockingIO() override;
  int SetDoNotFragment() override;
  int SetRecvTos() override;
  int SetTos(net::DiffServCodePoint dscp, net::EcnCodePoint ecn) override;
  void SetMsgConfirm(bool confirm) override;
  const net::NetLogWithSource& NetLog() const override;
  net::DscpAndEcn GetLastTos() const override;

 private:
  enum class State { kAssociating, kRelaying, kClosed };
  struct Outgoing {
    scoped_refptr<net::IOBuffer> data;
    int size;
    bool relayed;
    net::IPEndPoint to;
  };

  // The UDP ASSOCIATE handshake on the control connection.
  void OnConnected(int result);
  void OnMethod();
  void OnAuthenticated();
  void OnReplyHead();
  void OnReply();
  void Watch();
  void Fail();
  void Write(std::vector<uint8_t> bytes, base::OnceClosure then);
  void DoWrite();
  void OnWritten(int result);
  void Read(size_t size, base::OnceClosure then);
  void DoRead();
  void OnRead(int result);

  void Queue(Outgoing datagram);
  void Pump();
  void OnSent(int result);

  int ReadDatagram();
  void OnDatagram(int result);
  // The size of the datagram given to the reader, or nullopt for one that
  // came from neither the relay nor this host.
  std::optional<int> Accept(int size);
  net::IPEndPoint Answering(const net::IPEndPoint& from);

  const std::unique_ptr<net::UDPServerSocket> socket_;
  const std::optional<net::IPEndPoint> socks5_;
  const std::optional<net::Socks5Credentials> credentials_;
  const raw_ptr<net::ClientSocketFactory> client_socket_factory_;
  const net::NetworkTrafficAnnotationTag traffic_annotation_;
  // The identity's address, with the port held here.
  net::IPEndPoint local_;
  net::IPAddress loopback_;

  State state_ = State::kAssociating;
  std::unique_ptr<net::StreamSocket> control_;
  scoped_refptr<net::DrainableIOBuffer> control_out_;
  scoped_refptr<net::IOBufferWithSize> control_buf_;
  std::vector<uint8_t> control_in_;
  size_t control_want_ = 0;
  base::OnceClosure control_then_;
  net::IPEndPoint relay_;

  std::deque<Outgoing> out_;
  bool sending_ = false;

  scoped_refptr<net::IOBufferWithSize> datagram_;
  net::IPEndPoint datagram_from_;
  scoped_refptr<net::IOBuffer> reader_buf_;
  int reader_len_ = 0;
  raw_ptr<net::IPEndPoint> reader_from_ = nullptr;
  net::CompletionOnceCallback reader_callback_;

  // Servers sent to by name (a placeholder endpoint), and the address each
  // one's answers came from, once one has.
  std::map<net::IPEndPoint, std::optional<net::IPEndPoint>> named_;
};

}  // namespace network

#endif  // SERVICES_NETWORK_P2P_CAMOUWOLF_PROXIED_P2P_H_
