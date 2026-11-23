// Copyright 2026 The Camouwolf Authors.
// Use of this source code is governed by a BSD-style license.
//
// CamouwolfConfig — the single reader for the per-launch identity.
//
// This is the Chromium twin of Camoufox's `MaskConfig.hpp`. The launcher
// (the `camouwolf` package) serializes one JSON object of spoof keys and hands it to
// the browser through the environment as CAMOU_CONFIG_1..N (chunked, because
// Windows caps a single environment variable at 32767 chars and a shell at
// 2047). Every Chromium process — browser, renderer, GPU, utility — inherits
// that environment at fork/spawn, so each one reconstructs the same identity
// with no IPC and no plumbing through mojo.
//
// WHY ENVIRONMENT AND NOT COMMAND-LINE SWITCHES:
//   A switch-borne persona is world-readable at /proc/<pid>/cmdline, shows in
//   `ps`, and — for the values Chromium already forwards to renderers — appears
//   verbatim in the renderer's own command line, which a compromised renderer
//   can read back. Camoufox has always used the environment for exactly this
//   reason; Camouwolf keeps that property. Only the handful of switches Chromium
//   itself must see before this config is live (e.g. --lang) are passed as
//   switches, and the launcher keeps those coherent with the config.
//
// THREAD SAFETY:
//   The JSON is parsed exactly once, on first access, under a base::NoDestructor
//   + absl::once. Every accessor returns by value or by const ref into that
//   immutable parsed tree, so callers on the compositor, main, and worker
//   threads share it lock-free after initialization.
//
// PER-CONTEXT IDENTITIES:
//   A browser context (a Playwright context) may be given its own identity
//   (SetForContext). The browser answers for that context with ForContext() or
//   under a ScopedIdentity, and hands the JSON to each of the context's
//   renderers, which adopt it in place of the launch's (AdoptForProcess). A
//   renderer serves one browser context, so a process-wide identity is still
//   the right model there. docs/PATCHES.md describes the design.
//
// COST:
//   Accessors are called from hot paths (Navigator getters, canvas readback,
//   font lookup). Key lookups are a flat_map probe over a small tree; callers on
//   the hottest paths (canvas farble, font enumeration) should hoist the value
//   into a static/local rather than re-reading per pixel or per glyph.

#ifndef BASE_CAMOU_CONFIG_H_
#define BASE_CAMOU_CONFIG_H_

#include <array>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "base/base_export.h"
// Needed for the `friend class NoDestructor<CamouwolfConfig>` below: without the
// real template in scope, clang parses that as declaring a non-template class
// named NoDestructor, and the genuine definition then collides with it.
#include "base/no_destructor.h"
// DictValue / ListValue live at namespace scope in base/ as of Chromium 152;
// they were nested as Value::Dict / Value::List before, with no alias left
// behind.
#include "base/values.h"

namespace content {
class BrowserContext;
}  // namespace content

namespace base {

class BASE_EXPORT CamouwolfConfig {
 public:
  CamouwolfConfig(const CamouwolfConfig&) = delete;
  CamouwolfConfig& operator=(const CamouwolfConfig&) = delete;
  ~CamouwolfConfig();

  // The process-wide instance. First call parses CAMOU_CONFIG_*. Under a
  // ScopedIdentity, the identity that scope names.
  static const CamouwolfConfig& Get();

  // The identity `context` was given, or the launch's when it has none. Only
  // the address is used, so base needs no more of content than the name.
  static const CamouwolfConfig& ForContext(const content::BrowserContext* context);

  // Gives `context` its own identity. False, and nothing changes, when `json`
  // is not a JSON object.
  static bool SetForContext(const content::BrowserContext* context, std::string_view json);

  // Drops `context`'s identity; called as the context is destroyed, so a new
  // context at the same address starts with the launch's.
  static void EraseContext(const content::BrowserContext* context);

  // A process serving one context replaces the launch identity it inherited
  // with that context's. Call it before anything in the process reads one.
  static void AdoptForProcess(std::string_view json);

  // The key the context's JSON is handed to its renderers under, as a file
  // descriptor: the environment and the command line are fixed at the
  // zygote, and a switch would show the identity to anyone who can list
  // processes.
  static constexpr char kProcessFileKey[] = "context_identity";

  // While one is alive, Get() on this thread answers with `context`'s
  // identity, so the browser computes a context's user agent and client hints
  // through the same code as the launch's.
  class BASE_EXPORT ScopedIdentity {
   public:
    explicit ScopedIdentity(const content::BrowserContext* context);
    ScopedIdentity(const ScopedIdentity&) = delete;
    ScopedIdentity& operator=(const ScopedIdentity&) = delete;
    ~ScopedIdentity();

   private:
    const CamouwolfConfig* const previous_;
  };

  // The JSON a context's identity was given as; empty for the launch's.
  const std::string& json() const { return json_; }

  // True when the launch carried any config at all. A stock launch (no
  // launcher, someone running the binary by hand) has none, and every accessor
  // below then returns nullopt so all call sites fall through to upstream
  // Chromium behavior. This is the property that keeps the patches safe: an
  // unconfigured Camouwolf binary IS Chromium.
  bool IsActive() const { return active_; }

  bool Has(std::string_view key) const;

  std::optional<std::string> GetString(std::string_view key) const;
  std::optional<int> GetInt(std::string_view key) const;
  std::optional<uint32_t> GetUint32(std::string_view key) const;
  std::optional<double> GetDouble(std::string_view key) const;
  std::optional<bool> GetBool(std::string_view key) const;

  // Convenience: false when absent, rather than nullopt.
  bool CheckBool(std::string_view key) const;

  // A JSON array of strings, empty when absent or not an array.
  std::vector<std::string> GetStringList(std::string_view key) const;

  // Lowercased twin of GetStringList — font matching in Blink is
  // case-insensitive and comparing against a lowercased set avoids a per-lookup
  // allocation.
  std::vector<std::string> GetStringListLower(std::string_view key) const;

  // A dotted domain lookup: GetNested("webGl:parameters", "37445") reads
  // config["webGl:parameters"]["37445"]. Returns null when either level is
  // absent. Used by the WebGL and mediaDevices patches, which carry a map
  // rather than a flat key.
  const Value* GetNested(std::string_view domain, std::string_view key) const;

  // A JSON array / object as parsed, null when absent or of another type.
  // For values whose items are themselves objects (the UA-CH brand lists, the
  // WebGL shader-precision table) and for callers that iterate a map.
  const ListValue* GetList(std::string_view key) const;
  const DictValue* GetDict(std::string_view key) const;

  // [left, top, width, height] — the shape screen.availRect and the
  // window-geometry patches want.
  std::optional<std::array<int, 4>> GetIntRect(std::string_view key) const;

  // The whole parsed object, for patches that iterate (font list, voices).
  const DictValue& AsDict() const { return dict_; }

 private:
  friend class NoDestructor<CamouwolfConfig>;
  CamouwolfConfig();
  CamouwolfConfig(DictValue dict, std::string json);

  static CamouwolfConfig& Launch();

  DictValue dict_;
  bool active_ = false;
  std::string json_;
};

}  // namespace base

#endif  // BASE_CAMOU_CONFIG_H_
