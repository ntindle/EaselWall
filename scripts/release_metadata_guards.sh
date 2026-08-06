#!/usr/bin/env bash

# Shared, fail-closed parsers for the release workflow. This file is sourced by
# both preflight and mutation steps so a channel cannot pass one parser and then
# be changed under weaker rules later in the same run.

single_cask_version() {
  local file="$1" declarations values
  declarations=$(awk '/^[[:space:]]*version[[:space:]]+/ { count++ } END { print count + 0 }' "$file")
  if (( declarations != 1 )); then return 1; fi
  values=$(sed -nE 's/^[[:space:]]*version "([0-9]+\.[0-9]+\.[0-9]+)"[[:space:]]*$/\1/p' "$file")
  if [[ ! "$values" =~ ^(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})$ ]]; then
    return 1
  fi
  printf '%s\n' "$values"
}

single_cask_sha256() {
  local file="$1" declarations values
  declarations=$(awk '/^[[:space:]]*sha256[[:space:]]+/ { count++ } END { print count + 0 }' "$file")
  if (( declarations != 1 )); then return 1; fi
  values=$(sed -nE 's/^[[:space:]]*sha256 "([0-9A-Fa-f]{64})"[[:space:]]*$/\1/p' "$file")
  if [[ ! "$values" =~ ^[0-9A-Fa-f]{64}$ ]]; then return 1; fi
  printf '%s\n' "$values" | tr '[:upper:]' '[:lower:]'
}

single_appcast_value() {
  local kind="$1" file="$2" tag declarations values
  case "$kind" in
    version) tag="sparkle:shortVersionString" ;;
    build) tag="sparkle:version" ;;
    *) return 1 ;;
  esac
  declarations=$(awk -v needle="<$tag>" '{
    line = $0
    while ((position = index(line, needle)) > 0) {
      count++
      line = substr(line, position + length(needle))
    }
  } END { print count + 0 }' "$file")
  if (( declarations != 1 )); then return 1; fi
  if [[ "$kind" == "version" ]]; then
    values=$(sed -nE 's|.*<sparkle:shortVersionString>([^<]+)</sparkle:shortVersionString>.*|\1|p' "$file")
    if [[ ! "$values" =~ ^(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})$ ]]; then
      return 1
    fi
  else
    values=$(sed -nE 's|.*<sparkle:version>([^<]+)</sparkle:version>.*|\1|p' "$file")
    # Bash arithmetic is signed. Keep every numeric component within 18
    # digits so comparisons cannot overflow while still allowing realistic
    # timestamp-style build numbers.
    if [[ ! "$values" =~ ^(0|[1-9][0-9]{0,17})(\.(0|[1-9][0-9]{0,17})){0,2}$ ]]; then
      return 1
    fi
  fi
  printf '%s\n' "$values"
}

single_project_version() {
  local file="$1" declarations values
  declarations=$(awk '/^[[:space:]]*MARKETING_VERSION:[[:space:]]*/ { count++ } END { print count + 0 }' "$file")
  if (( declarations != 1 )); then return 1; fi
  values=$(sed -nE 's/^[[:space:]]*MARKETING_VERSION:[[:space:]]*"([0-9]+\.[0-9]+\.[0-9]+)"[[:space:]]*$/\1/p' "$file")
  if [[ ! "$values" =~ ^(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})$ ]]; then
    return 1
  fi
  printf '%s\n' "$values"
}

single_structured_version() {
  local file="$1" declarations values
  declarations=$(awk '{
    line = $0
    needle = "\"softwareVersion\""
    while ((position = index(line, needle)) > 0) {
      count++
      line = substr(line, position + length(needle))
    }
  } END { print count + 0 }' "$file")
  if (( declarations != 1 )); then return 1; fi
  values=$(sed -nE 's/.*"softwareVersion"[[:space:]]*:[[:space:]]*"([0-9]+\.[0-9]+\.[0-9]+)".*/\1/p' "$file")
  if [[ ! "$values" =~ ^(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})$ ]]; then
    return 1
  fi
  printf '%s\n' "$values"
}

single_homepage_lastmod() {
  local file="$1"
  python3 - "$file" <<'PY'
from datetime import date
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

path = Path(sys.argv[1])

try:
    root = ET.parse(path).getroot()
except (ET.ParseError, OSError):
    raise SystemExit(1)


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


homepage_values: list[str] = []
for url in (element for element in root.iter() if local_name(element.tag) == "url"):
    locs = [child for child in url if local_name(child.tag) == "loc"]
    if not any(
        (child.text or "").strip() == "https://easelwall.com/" for child in locs
    ):
        continue
    lastmods = [child for child in url if local_name(child.tag) == "lastmod"]
    if (
        len(locs) != 1
        or (locs[0].text or "").strip() != "https://easelwall.com/"
        or len(lastmods) != 1
    ):
        raise SystemExit(1)
    homepage_values.append((lastmods[0].text or "").strip())

if len(homepage_values) != 1:
    raise SystemExit(1)

value = homepage_values[0]
if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
    raise SystemExit(1)
try:
    parsed = date.fromisoformat(value)
except ValueError:
    raise SystemExit(1)
if parsed.isoformat() != value:
    raise SystemExit(1)
print(value)
PY
}

version_greater_than() {
  local left_part right_part index
  local -a left_parts right_parts
  IFS=. read -r -a left_parts <<< "$1"
  IFS=. read -r -a right_parts <<< "$2"
  for index in 0 1 2; do
    left_part=${left_parts[$index]}
    right_part=${right_parts[$index]}
    if (( 10#$left_part > 10#$right_part )); then return 0; fi
    if (( 10#$left_part < 10#$right_part )); then return 1; fi
  done
  return 1
}

# Print the one version currently published by all mutable channels. A prior
# version is a valid transition state, but disagreement or a version newer than
# the immutable release candidate is not.
single_current_published_version() {
  local candidate="$1" published current
  shift
  if (( $# < 2 )); then return 1; fi
  published="$1"
  shift
  for current in "$@"; do
    if [[ "$current" != "$published" ]]; then return 1; fi
  done
  if version_greater_than "$published" "$candidate"; then return 1; fi
  printf '%s\n' "$published"
}

build_greater_or_equal() {
  local left_part right_part index
  local -a left_parts right_parts
  IFS=. read -r -a left_parts <<< "$1"
  IFS=. read -r -a right_parts <<< "$2"
  for index in 0 1 2; do
    left_part=${left_parts[$index]:-0}
    right_part=${right_parts[$index]:-0}
    if (( 10#$left_part > 10#$right_part )); then return 0; fi
    if (( 10#$left_part < 10#$right_part )); then return 1; fi
  done
  return 0
}
