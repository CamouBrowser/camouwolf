// Copyright 2026 The Camouwolf Authors.
// Use of this source code is governed by a BSD-style license.

#include "services/network/p2p/camouwolf_proxied_p2p.h"

#include <algorithm>
#include <utility>

#include "base/camouwolf_config.h"
#include "base/functional/bind.h"
#include "base/no_destructor.h"
#include "base/strings/string_util.h"
#include "build/build_config.h"
#include "net/base/address_list.h"
#include "net/base/io_buffer.h"
#include "net/base/net_errors.h"
#include "net/base/network_change_notifier.h"
#include "net/base/network_handle.h"
#include "net/base/request_priority.h"
#include "net/base/sys_addrinfo.h"
#include "net/http/http_network_session.h"
#include "net/http/http_request_headers.h"
#include "net/log/net_log_source.h"
#include "net/log/net_log_with_source.h"
#include "net/net_buildflags.h"
#include "net/proxy_resolution/proxy_resolution_request.h"
#include "net/proxy_resolution/proxy_resolution_service.h"
#include "net/socket/client_socket_factory.h"
#include "net/socket/stream_socket.h"
#include "net/url_request/url_request_context.h"
#include "url/gurl.h"

#if BUILDFLAG(ENABLE_MDNS)
#include "services/network/mdns_responder.h"
#endif

namespace network {

namespace {

// Any URL a page's WebRTC traffic would be proxied like; the proxy settings
// Playwright and the launcher write apply to every URL alike.
constexpr char kProbeUrl[] = "https://webrtc.invalid/";

// RFC 1928 section 7: RSV, FRAG, ATYP, then up to a 255-byte name and a port.
constexpr size_t kMaxHeader = 4 + 1 + 255 + 2;
// Datagrams held while the association is made, or the socket is busy; a
// kernel's send buffer drops beyond its size too.
constexpr size_t kMaxQueued = 256;

// Names given as STUN or TURN servers, in placeholder order. A placeholder is
// in 240.0.0.0/4, reserved, so it is never a real server's address.
std::vector<std::string>& Names() {
  static base::NoDestructor<std::vector<std::string>> names;
  return *names;
}

net::IPAddress PlaceholderForName(const std::string& name) {
  std::vector<std::string>& names = Names();
  auto it = std::ranges::find(names, name);
  size_t index = it - names.begin() + 1;
  if (it == names.end()) {
    names.push_back(name);
  }
  return net::IPAddress(240, (index >> 16) & 0xff, (index >> 8) & 0xff,
                        index & 0xff);
}

std::optional<std::string> NameForPlaceholder(const net::IPAddress& address) {
  if (!address.IsIPv4() || address.bytes()[0] != 240) {
    return std::nullopt;
  }
  size_t index = (address.bytes()[1] << 16) | (address.bytes()[2] << 8) |
                 address.bytes()[3];
  if (index == 0 || index > Names().size()) {
    return std::nullopt;
  }
  return Names()[index - 1];
}

std::optional<net::IPAddress> IdentityAddress(int family) {
  const base::CamouwolfConfig& config = base::CamouwolfConfig::Get();
  std::optional<std::string> value = family == AF_INET
                                         ? config.GetString("webrtc:ipv4")
                                         : config.GetString("webrtc:ipv6");
  std::optional<net::IPAddress> address =
      value ? net::IPAddress::FromIPLiteral(*value) : std::nullopt;
  if (!address || address->IsIPv4() != (family == AF_INET)) {
    return std::nullopt;
  }
  return address;
}

void AppendAddress(std::vector<uint8_t>& out, const net::IPAddress& address) {
  out.push_back(address.IsIPv4() ? 1 : 4);
  out.insert(out.end(), address.bytes().begin(), address.bytes().end());
}

void AppendPort(std::vector<uint8_t>& out, uint16_t port) {
  out.push_back(port >> 8);
  out.push_back(port & 0xff);
}

uint16_t PortAt(base::span<const uint8_t> data, size_t at) {
  return (data[at] << 8) | data[at + 1];
}

std::unique_ptr<net::DatagramServerSocket> CreateUdpSocket(
    std::optional<net::IPEndPoint> socks5,
    std::optional<net::Socks5Credentials> credentials,
    net::ClientSocketFactory* client_socket_factory,
    const net::NetworkTrafficAnnotationTag& traffic_annotation,
    net::NetLog* net_log) {
  return std::make_unique<ProxiedUdpSocket>(socks5, credentials,
                                            client_socket_factory,
                                            traffic_annotation, net_log);
}

}  // namespace

ProxiedP2P::ProxiedP2P(
    net::URLRequestContext* context,
    const net::NetworkAnonymizationKey& network_anonymization_key)
    : context_(context),
      network_anonymization_key_(network_anonymization_key) {
  int result = context_->proxy_resolution_service()->ResolveProxy(
      GURL(kProbeUrl), net::HttpRequestHeaders::kPostMethod,
      network_anonymization_key_, net::handles::kInvalidNetworkHandle,
      &proxy_info_,
      base::BindOnce(&ProxiedP2P::OnProxyResolved, base::Unretained(this)),
      &proxy_request_, net::NetLogWithSource(), net::DEFAULT_PRIORITY);
  if (result != net::ERR_IO_PENDING) {
    OnProxyResolved(result);
  }
}

ProxiedP2P::~ProxiedP2P() = default;

void ProxiedP2P::Wait(base::OnceClosure then) {
  waiting_.push_back(std::move(then));
}

void ProxiedP2P::OnProxyResolved(int result) {
  proxy_request_.reset();
  if (result == net::OK && proxy_info_.is_direct()) {
    Done(Route::kDirect);
    return;
  }
  // Anything but DIRECT is proxied, a failure included; only a lone SOCKS5
  // proxy can carry UDP.
  if (result != net::OK || !proxy_info_.proxy_chain().is_single_proxy() ||
      proxy_info_.proxy_chain().First().scheme() !=
          net::ProxyServer::SCHEME_SOCKS5) {
    Done(Route::kProxied);
    return;
  }
  credentials_ = net::Socks5CredentialsFor(
      proxy_info_.proxy_chain().First().host_port_pair());
  host_request_ = context_->host_resolver()->CreateRequest(
      proxy_info_.proxy_chain().First().host_port_pair(),
      network_anonymization_key_, net::handles::kInvalidNetworkHandle,
      net::NetLogWithSource(), std::nullopt);
  result = host_request_->Start(
      base::BindOnce(&ProxiedP2P::OnProxyHostResolved, base::Unretained(this)));
  if (result != net::ERR_IO_PENDING) {
    OnProxyHostResolved(result);
  }
}

void ProxiedP2P::OnProxyHostResolved(int result) {
  const net::AddressList& addresses = host_request_->GetAddressResults();
  if (result == net::OK && !addresses.empty()) {
    socks5_ = addresses.front();
  }
  host_request_.reset();
  Done(Route::kProxied);
}

void ProxiedP2P::Done(Route route) {
  route_ = route;
  for (base::OnceClosure& then : std::exchange(waiting_, {})) {
    std::move(then).Run();
  }
}

// static
net::NetworkInterfaceList ProxiedP2P::Network() {
  net::NetworkInterfaceList list;
  for (int family : {AF_INET, AF_INET6}) {
    if (std::optional<net::IPAddress> address = IdentityAddress(family)) {
      list.emplace_back("eth0", "eth0", 2,
                        net::NetworkChangeNotifier::CONNECTION_ETHERNET,
                        *address, family == AF_INET ? 24 : 64,
                        net::IP_ADDRESS_ATTRIBUTE_NONE);
    }
  }
  return list;
}

// static
net::IPAddress ProxiedP2P::DefaultAddress(int family) {
  return IdentityAddress(family).value_or(net::IPAddress());
}

// static
net::IPAddressList ProxiedP2P::Resolve(
    const std::string& host_name,
    std::optional<net::AddressFamily> family) {
  std::string name(base::TrimString(host_name, ".", base::TRIM_TRAILING));
  if (base::EndsWith(name, ".local", base::CompareCase::INSENSITIVE_ASCII)) {
    // A multicast query would go out on this machine's network; only the
    // names this browser made for its own candidates are answered.
#if BUILDFLAG(ENABLE_MDNS)
    std::optional<net::IPAddress> own = MdnsResponder::AddressForOwnName(name);
    if (own && (!family || *family == net::ADDRESS_FAMILY_UNSPECIFIED ||
                *family == net::GetAddressFamily(*own))) {
      return {*own};
    }
#endif
    return {};
  }
  // The proxy resolves the name, to one address of its choosing, so only the
  // IPv4 socket asks by name, as it alone would on an IPv4 exit.
  if (name.empty() || name.size() > 255 ||
      family == net::ADDRESS_FAMILY_IPV6) {
    return {};
  }
  return {PlaceholderForName(name)};
}

P2PSocketUdp::DatagramServerSocketFactory ProxiedP2P::UdpSocketFactory(
    const net::NetworkTrafficAnnotationTag& traffic_annotation) const {
  return base::BindRepeating(
      &CreateUdpSocket, socks5_, credentials_,
      base::Unretained(
          context_->GetNetworkSessionContext()->client_socket_factory.get()),
      traffic_annotation);
}

ProxiedUdpSocket::ProxiedUdpSocket(
    std::optional<net::IPEndPoint> socks5,
    std::optional<net::Socks5Credentials> credentials,
    net::ClientSocketFactory* client_socket_factory,
    const net::NetworkTrafficAnnotationTag& traffic_annotation,
    net::NetLog* net_log)
    : socket_(std::make_unique<net::UDPServerSocket>(net_log,
                                                     net::NetLogSource())),
      socks5_(socks5),
      credentials_(std::move(credentials)),
      client_socket_factory_(client_socket_factory),
      traffic_annotation_(traffic_annotation) {
#if BUILDFLAG(IS_WIN)
  socket_->UseNonBlockingIO();
#endif
}

ProxiedUdpSocket::~ProxiedUdpSocket() = default;

int ProxiedUdpSocket::Listen(const net::IPEndPoint& address) {
  // The socket here is in the proxy's family, on every interface, since the
  // identity's address is not this machine's.
  bool ipv4 = !socks5_ || socks5_->address().IsIPv4();
  loopback_ =
      ipv4 ? net::IPAddress::IPv4Localhost() : net::IPAddress::IPv6Localhost();
  int result = socket_->Listen(net::IPEndPoint(
      ipv4 ? net::IPAddress::IPv4AllZeros() : net::IPAddress::IPv6AllZeros(),
      address.port()));
  net::IPEndPoint bound;
  if (result == net::OK) {
    result = socket_->GetLocalAddress(&bound);
  }
  if (result != net::OK) {
    return result;
  }
  local_ = net::IPEndPoint(address.address(), bound.port());
  if (!socks5_) {
    state_ = State::kClosed;
    return net::OK;
  }
  control_ = client_socket_factory_->CreateTransportClientSocket(
      net::AddressList(*socks5_), net::handles::kInvalidNetworkHandle,
      nullptr, nullptr, socket_->NetLog().net_log(), net::NetLogSource());
  control_buf_ = base::MakeRefCounted<net::IOBufferWithSize>(512);
  result = control_->Connect(
      base::BindOnce(&ProxiedUdpSocket::OnConnected, base::Unretained(this)));
  if (result != net::ERR_IO_PENDING) {
    OnConnected(result);
  }
  return net::OK;
}

int ProxiedUdpSocket::SetReceiveBufferSize(int32_t size) {
  return socket_->SetReceiveBufferSize(size);
}

int ProxiedUdpSocket::SetSendBufferSize(int32_t size) {
  return socket_->SetSendBufferSize(size);
}

void ProxiedUdpSocket::AllowAddressReuse() {
  socket_->AllowAddressReuse();
}

void ProxiedUdpSocket::AllowBroadcast() {
  socket_->AllowBroadcast();
}

void ProxiedUdpSocket::AllowAddressSharingForMulticast() {
  socket_->AllowAddressSharingForMulticast();
}

int ProxiedUdpSocket::JoinGroup(const net::IPAddress& group_address) const {
  return socket_->JoinGroup(group_address);
}

int ProxiedUdpSocket::LeaveGroup(const net::IPAddress& group_address) const {
  return socket_->LeaveGroup(group_address);
}

int ProxiedUdpSocket::SetMulticastInterface(uint32_t interface_index) {
  return socket_->SetMulticastInterface(interface_index);
}

int ProxiedUdpSocket::SetMulticastTimeToLive(int time_to_live) {
  return socket_->SetMulticastTimeToLive(time_to_live);
}

int ProxiedUdpSocket::SetMulticastLoopbackMode(bool loopback) {
  return socket_->SetMulticastLoopbackMode(loopback);
}

int ProxiedUdpSocket::SetDiffServCodePoint(net::DiffServCodePoint dscp) {
  return socket_->SetDiffServCodePoint(dscp);
}

void ProxiedUdpSocket::DetachFromThread() {
  socket_->DetachFromThread();
}

int ProxiedUdpSocket::GetPeerAddress(net::IPEndPoint* address) const {
  return socket_->GetPeerAddress(address);
}

void ProxiedUdpSocket::UseNonBlockingIO() {
  socket_->UseNonBlockingIO();
}

int ProxiedUdpSocket::SetDoNotFragment() {
  return socket_->SetDoNotFragment();
}

int ProxiedUdpSocket::SetRecvTos() {
  return socket_->SetRecvTos();
}

int ProxiedUdpSocket::SetTos(net::DiffServCodePoint dscp, net::EcnCodePoint ecn) {
  return socket_->SetTos(dscp, ecn);
}

void ProxiedUdpSocket::SetMsgConfirm(bool confirm) {
  socket_->SetMsgConfirm(confirm);
}

const net::NetLogWithSource& ProxiedUdpSocket::NetLog() const {
  return socket_->NetLog();
}

net::DscpAndEcn ProxiedUdpSocket::GetLastTos() const {
  return socket_->GetLastTos();
}

void ProxiedUdpSocket::OnConnected(int result) {
  if (result != net::OK) {
    Fail();
    return;
  }
  Write(net::Socks5Greeting(credentials_.has_value()),
        base::BindOnce(&ProxiedUdpSocket::Read, base::Unretained(this), 2,
                       base::BindOnce(&ProxiedUdpSocket::OnMethod,
                                      base::Unretained(this))));
}

void ProxiedUdpSocket::OnMethod() {
  // Method 2 is username/password (RFC 1929), offered only with credentials.
  const bool password = credentials_ && control_in_[1] == 2;
  if (control_in_[0] != 5 || (control_in_[1] != 0 && !password)) {
    Fail();
    return;
  }
  control_in_.clear();
  if (password) {
    Write(net::Socks5PasswordRequest(*credentials_),
          base::BindOnce(&ProxiedUdpSocket::Read, base::Unretained(this), 2,
                         base::BindOnce(&ProxiedUdpSocket::OnAuthenticated,
                                        base::Unretained(this))));
    return;
  }
  OnAuthenticated();
}

void ProxiedUdpSocket::OnAuthenticated() {
  if (!control_in_.empty() && control_in_[1] != 0) {
    Fail();
    return;
  }
  control_in_.clear();
  // UDP ASSOCIATE, from an address not known yet (RFC 1928 section 4).
  std::vector<uint8_t> request = {5, 3, 0};
  AppendAddress(request, socks5_->address().IsIPv4()
                             ? net::IPAddress::IPv4AllZeros()
                             : net::IPAddress::IPv6AllZeros());
  AppendPort(request, 0);
  Write(std::move(request),
        base::BindOnce(&ProxiedUdpSocket::Read, base::Unretained(this), 5,
                       base::BindOnce(&ProxiedUdpSocket::OnReplyHead,
                                      base::Unretained(this))));
}

void ProxiedUdpSocket::OnReplyHead() {
  const uint8_t type = control_in_[3];
  if (control_in_[0] != 5 || control_in_[1] != 0 ||
      (type != 1 && type != 3 && type != 4)) {
    Fail();
    return;
  }
  Read(type == 1 ? 10 : type == 4 ? 22 : 7 + control_in_[4],
       base::BindOnce(&ProxiedUdpSocket::OnReply, base::Unretained(this)));
}

void ProxiedUdpSocket::OnReply() {
  base::span<const uint8_t> reply(control_in_);
  const uint8_t type = reply[3];
  net::IPAddress bound;
  if (type != 3) {
    bound = net::IPAddress(reply.subspan(4u, type == 1 ? 4u : 16u));
  }
  // An unspecified BND.ADDR means the proxy's own address (RFC 1928 section
  // 6); so does a name, or a private address from a public proxy, which is
  // the proxy's side of a NAT.
  if (!bound.IsValid() || bound.IsZero() ||
      (!bound.IsPubliclyRoutable() &&
       socks5_->address().IsPubliclyRoutable())) {
    bound = socks5_->address();
  }
  relay_ = net::IPEndPoint(bound, PortAt(reply, reply.size() - 2));
  state_ = State::kRelaying;
  Pump();
  Watch();
}

// The association lasts as long as the control connection (RFC 1928
// section 7).
void ProxiedUdpSocket::Watch() {
  control_in_.clear();
  Read(1, base::BindOnce(&ProxiedUdpSocket::Watch, base::Unretained(this)));
}

void ProxiedUdpSocket::Fail() {
  state_ = State::kClosed;
  control_.reset();
  Pump();
}

void ProxiedUdpSocket::Write(std::vector<uint8_t> bytes,
                             base::OnceClosure then) {
  size_t size = bytes.size();
  control_out_ = base::MakeRefCounted<net::DrainableIOBuffer>(
      base::MakeRefCounted<net::VectorIOBuffer>(std::move(bytes)), size);
  control_then_ = std::move(then);
  DoWrite();
}

void ProxiedUdpSocket::DoWrite() {
  while (control_out_->BytesRemaining() > 0) {
    int result = control_->Write(
        control_out_.get(), control_out_->BytesRemaining(),
        base::BindOnce(&ProxiedUdpSocket::OnWritten, base::Unretained(this)),
        traffic_annotation_);
    if (result == net::ERR_IO_PENDING) {
      return;
    }
    if (result <= 0) {
      Fail();
      return;
    }
    control_out_->DidConsume(result);
  }
  std::move(control_then_).Run();
}

void ProxiedUdpSocket::OnWritten(int result) {
  if (result <= 0) {
    Fail();
    return;
  }
  control_out_->DidConsume(result);
  DoWrite();
}

void ProxiedUdpSocket::Read(size_t size, base::OnceClosure then) {
  control_want_ = size;
  control_then_ = std::move(then);
  DoRead();
}

void ProxiedUdpSocket::DoRead() {
  while (control_in_.size() < control_want_) {
    int result = control_->Read(
        control_buf_.get(), control_buf_->size(),
        base::BindOnce(&ProxiedUdpSocket::OnRead, base::Unretained(this)));
    if (result == net::ERR_IO_PENDING) {
      return;
    }
    if (result <= 0) {
      Fail();
      return;
    }
    auto got = control_buf_->first(static_cast<size_t>(result));
    control_in_.insert(control_in_.end(), got.begin(), got.end());
  }
  std::move(control_then_).Run();
}

void ProxiedUdpSocket::OnRead(int result) {
  if (result <= 0) {
    Fail();
    return;
  }
  auto got = control_buf_->first(static_cast<size_t>(result));
  control_in_.insert(control_in_.end(), got.begin(), got.end());
  DoRead();
}

int ProxiedUdpSocket::SendTo(net::IOBuffer* buf,
                             int buf_len,
                             const net::IPEndPoint& address,
                             net::CompletionOnceCallback callback) {
  auto payload = buf->first(static_cast<size_t>(buf_len));
  if (address.address().IsLoopback() ||
      address.address() == local_.address()) {
    // The other end of a connection on this host, which the page knows by
    // the identity's address.
    Queue({base::MakeRefCounted<net::VectorIOBuffer>(payload), buf_len, false,
           net::IPEndPoint(loopback_, address.port())});
  } else if (state_ != State::kClosed) {
    std::vector<uint8_t> datagram = {0, 0, 0};
    if (std::optional<std::string> name =
            NameForPlaceholder(address.address())) {
      datagram.push_back(3);
      datagram.push_back(static_cast<uint8_t>(name->size()));
      datagram.insert(datagram.end(), name->begin(), name->end());
      named_.try_emplace(address);
    } else {
      AppendAddress(datagram, address.address());
    }
    AppendPort(datagram, address.port());
    datagram.insert(datagram.end(), payload.begin(), payload.end());
    int size = static_cast<int>(datagram.size());
    Queue({base::MakeRefCounted<net::VectorIOBuffer>(std::move(datagram)),
           size, true, net::IPEndPoint()});
  }
  // A datagram is handed on whole or lost, as a kernel's are.
  return buf_len;
}

void ProxiedUdpSocket::Queue(Outgoing datagram) {
  if (out_.size() < kMaxQueued) {
    out_.push_back(std::move(datagram));
  }
  Pump();
}

void ProxiedUdpSocket::Pump() {
  while (!out_.empty() && !sending_) {
    Outgoing& next = out_.front();
    if (next.relayed && state_ == State::kAssociating) {
      return;
    }
    if (next.relayed && state_ == State::kClosed) {
      out_.pop_front();
      continue;
    }
    if (next.relayed) {
      next.to = relay_;
    }
    int result = socket_->SendTo(
        next.data.get(), next.size, next.to,
        base::BindOnce(&ProxiedUdpSocket::OnSent, base::Unretained(this)));
    if (result == net::ERR_IO_PENDING) {
      sending_ = true;
      return;
    }
    out_.pop_front();
  }
}

void ProxiedUdpSocket::OnSent(int result) {
  sending_ = false;
  out_.pop_front();
  Pump();
}

int ProxiedUdpSocket::RecvFrom(net::IOBuffer* buf,
                               int buf_len,
                               net::IPEndPoint* address,
                               net::CompletionOnceCallback callback) {
  reader_buf_ = buf;
  reader_len_ = buf_len;
  reader_from_ = address;
  int result = ReadDatagram();
  if (result == net::ERR_IO_PENDING) {
    reader_callback_ = std::move(callback);
  } else {
    reader_buf_ = nullptr;
    reader_from_ = nullptr;
  }
  return result;
}

int ProxiedUdpSocket::ReadDatagram() {
  while (true) {
    size_t size = static_cast<size_t>(reader_len_) + kMaxHeader;
    if (!datagram_ || static_cast<size_t>(datagram_->size()) < size) {
      datagram_ = base::MakeRefCounted<net::IOBufferWithSize>(size);
    }
    int result = socket_->RecvFrom(
        datagram_.get(), datagram_->size(), &datagram_from_,
        base::BindOnce(&ProxiedUdpSocket::OnDatagram, base::Unretained(this)));
    if (result < 0) {
      return result;
    }
    if (std::optional<int> accepted = Accept(result)) {
      return *accepted;
    }
  }
}

void ProxiedUdpSocket::OnDatagram(int result) {
  if (result >= 0) {
    std::optional<int> accepted = Accept(result);
    result = accepted ? *accepted : ReadDatagram();
  }
  if (result == net::ERR_IO_PENDING) {
    return;
  }
  reader_buf_ = nullptr;
  reader_from_ = nullptr;
  std::move(reader_callback_).Run(result);
}

std::optional<int> ProxiedUdpSocket::Accept(int size) {
  base::span<const uint8_t> data =
      datagram_->first(static_cast<size_t>(size));
  net::IPEndPoint from;
  if (state_ == State::kRelaying && datagram_from_ == relay_) {
    // Only whole datagrams: no proxy fragments in practice.
    if (data.size() < 5 || data[0] || data[1] || data[2]) {
      return std::nullopt;
    }
    size_t header = data[3] == 1 ? 10 : data[3] == 4 ? 22 : 7 + data[4];
    if ((data[3] != 1 && data[3] != 3 && data[3] != 4) ||
        data.size() < header) {
      return std::nullopt;
    }
    net::IPAddress source =
        data[3] == 3 ? PlaceholderForName(std::string(
                           data.begin() + 5, data.begin() + 5 + data[4]))
                     : net::IPAddress(data.subspan(4u, header - 6));
    from = Answering(net::IPEndPoint(source, PortAt(data, header - 2)));
    data = data.subspan(header);
  } else if (datagram_from_.address().IsLoopback()) {
    from = net::IPEndPoint(local_.address(), datagram_from_.port());
  } else {
    // Nothing reaches the identity's address but through the relay.
    return std::nullopt;
  }
  if (data.size() > static_cast<size_t>(reader_len_)) {
    return std::nullopt;
  }
  reader_buf_->span().copy_prefix_from(data);
  *reader_from_ = from;
  return static_cast<int>(data.size());
}

// A server sent to by name answers from the address the proxy resolved the
// name to; WebRTC knows it by the name's placeholder. The first answer on
// that port from an unknown address is taken to be the server's.
net::IPEndPoint ProxiedUdpSocket::Answering(const net::IPEndPoint& from) {
  for (const auto& [placeholder, answered_from] : named_) {
    if (answered_from == from) {
      return placeholder;
    }
  }
  for (auto& [placeholder, answered_from] : named_) {
    if (!answered_from && placeholder.port() == from.port()) {
      answered_from = from;
      return placeholder;
    }
  }
  return from;
}

int ProxiedUdpSocket::GetLocalAddress(net::IPEndPoint* address) const {
  if (local_.address().empty()) {
    return net::ERR_SOCKET_NOT_CONNECTED;
  }
  *address = local_;
  return net::OK;
}

void ProxiedUdpSocket::Close() {
  control_.reset();
  socket_->Close();
}

}  // namespace network
