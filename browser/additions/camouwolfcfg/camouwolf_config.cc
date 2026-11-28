// Copyright 2026 The Camouwolf Authors.
// Use of this source code is governed by a BSD-style license.

#include "base/camouwolf_config.h"

#include <algorithm>
#include <map>
#include <utility>

#include "base/check.h"
#include "base/cpu.h"
#include "base/environment.h"
#include "base/json/json_reader.h"
#include "base/logging.h"
#include "base/memory/ptr_util.h"
#include "base/no_destructor.h"
#include "base/strings/strcat.h"
#include "base/strings/string_number_conversions.h"
#include "base/synchronization/lock.h"
#include "build/build_config.h"

namespace base {

namespace {

// Reassemble CAMOU_CONFIG_1, CAMOU_CONFIG_2, ... in order and stop at the first
// gap. The launcher chunks at 2047 chars on Windows and 32767 elsewhere; the
// chunk boundary can fall mid-token, so the pieces are concatenated BEFORE
// parsing rather than parsed individually.
std::string ReadChunkedConfig() {
  std::unique_ptr<Environment> env = Environment::Create();
  std::string joined;
  for (int index = 1;; ++index) {
    const std::string name = StrCat({"CAMOU_CONFIG_", NumberToString(index)});
    std::optional<std::string> chunk = env->GetVar(name);
    if (!chunk.has_value()) {
      break;
    }
    joined += chunk.value();
  }
  if (joined.empty()) {
    // Single-variable fallback, for hand-driving the binary in a shell.
    if (std::optional<std::string> whole = env->GetVar("CAMOU_CONFIG")) {
      joined = whole.value();
    }
  }
  return joined;
}

std::optional<DictValue> ParseObject(std::string_view json) {
  std::optional<Value> parsed = JSONReader::Read(json, JSON_PARSE_RFC);
  if (!parsed.has_value() || !parsed->is_dict()) {
    return std::nullopt;
  }
  return std::move(*parsed).TakeDict();
}

// The identities given to browser contexts. Entries are read on the UI thread
// and erased only as their context is destroyed there, so a reference handed
// out stays valid for as long as its caller can hold the context.
struct ContextIdentities {
  Lock lock;
  std::map<const content::BrowserContext*, std::unique_ptr<CamouwolfConfig>> by_context;
};

ContextIdentities& Contexts() {
  static NoDestructor<ContextIdentities> contexts;
  return *contexts;
}

constinit thread_local const CamouwolfConfig* g_scoped = nullptr;

}  // namespace

CamouwolfConfig::CamouwolfConfig() {
  const std::string json = ReadChunkedConfig();
  if (json.empty()) {
    return;
  }
  std::optional<Value> parsed = JSONReader::Read(json, JSON_PARSE_RFC);
  if (!parsed.has_value() || !parsed->is_dict()) {
    // Loud, once, on stderr. Silently degrading to "no config" would ship a
    // stock-Chromium fingerprint under a stealth launch, which is the single
    // worst failure mode this project has: it looks like it worked.
    LOG(ERROR) << "CAMOU_CONFIG is not a valid JSON object — the launch will "
                  "run with a STOCK Chromium fingerprint.";
    return;
  }
  dict_ = std::move(*parsed).TakeDict();
  active_ = true;
}

CamouwolfConfig::CamouwolfConfig(DictValue dict, std::string json)
    : dict_(std::move(dict)), active_(true), json_(std::move(json)) {}

CamouwolfConfig::~CamouwolfConfig() = default;

// static
CamouwolfConfig& CamouwolfConfig::Launch() {
  static NoDestructor<CamouwolfConfig> instance;
  return *instance;
}

// static
const CamouwolfConfig& CamouwolfConfig::Get() {
  return g_scoped ? *g_scoped : Launch();
}

// static
const CamouwolfConfig& CamouwolfConfig::ForContext(const content::BrowserContext* context) {
  ContextIdentities& contexts = Contexts();
  AutoLock lock(contexts.lock);
  auto it = contexts.by_context.find(context);
  return it == contexts.by_context.end() ? Launch() : *it->second;
}

// static
bool CamouwolfConfig::SetForContext(const content::BrowserContext* context,
                                    std::string_view json) {
  std::optional<DictValue> dict = ParseObject(json);
  if (!dict) {
    return false;
  }
  ContextIdentities& contexts = Contexts();
  AutoLock lock(contexts.lock);
  contexts.by_context[context] =
      WrapUnique(new CamouwolfConfig(std::move(*dict), std::string(json)));
  return true;
}

// static
void CamouwolfConfig::EraseContext(const content::BrowserContext* context) {
  ContextIdentities& contexts = Contexts();
  AutoLock lock(contexts.lock);
  contexts.by_context.erase(context);
}

// static
void CamouwolfConfig::AdoptForProcess(std::string_view json) {
  std::optional<DictValue> dict = ParseObject(json);
  // The browser parsed this same JSON before accepting it, so a failure here
  // is a broken handoff; running on with the launch's identity would put the
  // renderer in a different identity from its context's network requests.
  CHECK(dict) << "context identity is not a JSON object";
  CamouwolfConfig& launch = Launch();
  launch.dict_ = std::move(*dict);
  launch.active_ = true;
  launch.json_ = std::string(json);
}

CamouwolfConfig::ScopedIdentity::ScopedIdentity(const content::BrowserContext* context)
    : previous_(g_scoped) {
  g_scoped = &ForContext(context);
}

CamouwolfConfig::ScopedIdentity::~ScopedIdentity() {
  g_scoped = previous_;
}

bool CamouwolfConfig::Has(std::string_view key) const {
  return dict_.Find(key) != nullptr;
}

std::optional<std::string> CamouwolfConfig::GetString(std::string_view key) const {
  const std::string* value = dict_.FindString(key);
  if (!value) {
    return std::nullopt;
  }
  return *value;
}

std::optional<int> CamouwolfConfig::GetInt(std::string_view key) const {
  if (std::optional<int> value = dict_.FindInt(key)) {
    return value;
  }
  // A JSON number that arrived as a double (the launcher emits devicePixelRatio
  // and the integers through the same serializer) still answers here.
  if (std::optional<double> value = dict_.FindDouble(key)) {
    return static_cast<int>(*value);
  }
  return std::nullopt;
}

std::optional<uint32_t> CamouwolfConfig::GetUint32(std::string_view key) const {
  if (std::optional<double> value = dict_.FindDouble(key)) {
    if (*value < 0.0) {
      return std::nullopt;
    }
    return static_cast<uint32_t>(*value);
  }
  if (std::optional<int> value = dict_.FindInt(key)) {
    return *value < 0 ? std::nullopt
                      : std::optional<uint32_t>(static_cast<uint32_t>(*value));
  }
  return std::nullopt;
}

std::optional<double> CamouwolfConfig::GetDouble(std::string_view key) const {
  return dict_.FindDouble(key);
}

std::optional<bool> CamouwolfConfig::GetBool(std::string_view key) const {
  return dict_.FindBool(key);
}

bool CamouwolfConfig::CheckBool(std::string_view key) const {
  return dict_.FindBool(key).value_or(false);
}

CamouwolfConfig::Cpu CamouwolfConfig::EmulatedCpu() const {
  const std::optional<std::string> claim = GetString("cpu:identity");
  if (!claim) {
    return Cpu::kHost;
  }
  // The launcher writes only these; anything else is a config no real machine
  // has, and running it as the host would hide that.
  CHECK(*claim == "intel" || *claim == "amd" || *claim == "arm64") << "cpu:identity " << *claim;
  const Cpu claimed = *claim == "intel" ? Cpu::kIntel : *claim == "amd" ? Cpu::kAmd : Cpu::kArm64;
#if defined(ARCH_CPU_X86_FAMILY)
  static const Cpu host = [] {
    const std::string vendor = base::CPU().vendor_name();
    return vendor == "GenuineIntel" ? Cpu::kIntel : vendor == "AuthenticAMD" ? Cpu::kAmd : Cpu::kHost;
  }();
  return claimed == host ? Cpu::kHost : claimed;
#else
  return Cpu::kHost;
#endif
}

std::vector<std::string> CamouwolfConfig::GetStringList(
    std::string_view key) const {
  std::vector<std::string> result;
  const ListValue* list = dict_.FindList(key);
  if (!list) {
    return result;
  }
  result.reserve(list->size());
  for (const Value& item : *list) {
    if (item.is_string()) {
      result.push_back(item.GetString());
    }
  }
  return result;
}

std::vector<std::string> CamouwolfConfig::GetStringListLower(
    std::string_view key) const {
  std::vector<std::string> result = GetStringList(key);
  for (std::string& item : result) {
    std::transform(item.begin(), item.end(), item.begin(), [](unsigned char c) {
      return static_cast<char>(std::tolower(c));
    });
  }
  return result;
}

std::optional<bool> CamouwolfConfig::ListContains(
    std::string_view key,
    std::string_view item) const {
  const ListValue* list = dict_.FindList(key);
  if (!list) {
    return std::nullopt;
  }
  for (const Value& entry : *list) {
    if (entry.is_string() && entry.GetString() == item) {
      return true;
    }
  }
  return false;
}

const Value* CamouwolfConfig::GetNested(std::string_view domain,
                                    std::string_view key) const {
  const DictValue* nested = dict_.FindDict(domain);
  if (!nested) {
    return nullptr;
  }
  return nested->Find(key);
}

const ListValue* CamouwolfConfig::GetList(std::string_view key) const {
  return dict_.FindList(key);
}

const DictValue* CamouwolfConfig::GetDict(std::string_view key) const {
  return dict_.FindDict(key);
}

std::optional<std::array<int, 4>> CamouwolfConfig::GetIntRect(
    std::string_view key) const {
  const ListValue* list = dict_.FindList(key);
  if (!list || list->size() != 4u) {
    return std::nullopt;
  }
  std::array<int, 4> rect{};
  for (size_t i = 0; i < 4u; ++i) {
    const Value& item = (*list)[i];
    if (item.is_int()) {
      rect[i] = item.GetInt();
    } else if (item.is_double()) {
      rect[i] = static_cast<int>(item.GetDouble());
    } else {
      return std::nullopt;
    }
  }
  return rect;
}

}  // namespace base
