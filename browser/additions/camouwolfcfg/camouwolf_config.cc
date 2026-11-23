// Copyright 2026 The Camouwolf Authors.
// Use of this source code is governed by a BSD-style license.

#include "base/camouwolf_config.h"

#include <algorithm>

#include "base/environment.h"
#include "base/json/json_reader.h"
#include "base/logging.h"
#include "base/no_destructor.h"
#include "base/strings/strcat.h"
#include "base/strings/string_number_conversions.h"

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

CamouwolfConfig::~CamouwolfConfig() = default;

// static
const CamouwolfConfig& CamouwolfConfig::Get() {
  static NoDestructor<CamouwolfConfig> instance;
  return *instance;
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
  // A JSON number that arrived as a double (the launcher emits 1e9-scale seeds
  // and devicePixelRatio through the same serializer) still answers here.
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
