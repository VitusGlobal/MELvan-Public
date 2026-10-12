#!/usr/bin/env bash

set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

status=0
fail() {
  printf 'FAIL  %s\n' "$1" >&2
  status=1
}

for required in README.md contents.md LICENSE.md NOTICE.md CITATION.cff \
  DOCUMENTATION-LICENSE.md SECURITY.md CONTRIBUTING.md CODE_OF_CONDUCT.md \
  THIRD_PARTY_NOTICES.md ASSET-LICENSES.md docs/architecture.md \
  docs/configuration.md docs/azure-deployment.md \
  docs/testing.md docs/security-and-privacy.md docs/principles.md \
  docs/foundational-references.md docs/health-domain-safeguards.md \
  infra/production-foundation.bicep infra/production-migration-job.bicep \
  infra/production-app.bicep scripts/deploy-azure-production.sh; do
  [[ -f "$required" ]] || fail "missing $required"
done

[[ -f LICENSE.md ]] || \
  fail "no software license has been selected"

publication_pattern='(sprint[- _]?[0-9]|development notes|acceptance transcript|MartinMBCN|Martin Mills|rg-melvan-demo-canadacentral|melvan\.vitus\.global|/Users/martin)'
if command -v rg >/dev/null 2>&1; then
  publication_matches=$(rg -n -i \
    --glob '!scripts/check-publication.sh' \
    --glob '!LICENSE.md' \
    --glob '!NOTICE.md' \
    --glob '!CITATION.cff' \
    --glob '!src/melvan/static/vendor/**' \
    --glob '!.venv*/**' \
    "$publication_pattern" . || true)
else
  publication_matches=$(grep -RInE \
    --exclude='check-publication.sh' \
    --exclude='LICENSE.md' \
    --exclude='NOTICE.md' \
    --exclude='CITATION.cff' \
    --exclude-dir='.git' \
    --exclude-dir='.venv*' \
    --exclude-dir='vendor' \
    "$publication_pattern" . || true)
fi
publication_matches=$(printf '%s\n' "$publication_matches" \
  | grep -vF 'Open-source AI product architecture:</strong> Martin Mills (3den Systems)' \
  | grep -vF 'Demo — <a href="http://melvan.vitus.global"' || true)
if [[ -n "$publication_matches" ]]; then
  printf '%s\n' "$publication_matches"
  fail "internal chronology, identity, path, or deployment value remains"
fi

if find . \( -path './.git' -o -path './.venv*' \) -prune -o -type f \
  \( -name '.env' -o -name '*.sqlite' -o -name '*.sqlite3' \
  -o -name '*.db' -o -name 'users.json' -o -name '.DS_Store' \) \
  -print | grep -q .; then
  fail "runtime or operating-system data is present"
fi

if find docs -type f \( -name '*.pdf' -o -name '*.pptx' -o -name '*.docx' \) \
  | grep -q .; then
  fail "unreviewed binary documentation is present"
fi

if grep -nH 'PENDING' ASSET-LICENSES.md SECURITY.md CODE_OF_CONDUCT.md; then
  fail "publication governance or asset rights remain unresolved"
fi

for canonical in \
  'results_based_management-gestion_axee_resultats-guide.aspx?lang=eng' \
  'wkkf.issuelab.org/resource/logic-model-development-guide.html' \
  'tired-of-vague-advice-on-writing-a-theory-of-change-narrative-try-the-recap-framework/' \
  'Performance Measurement Framework' \
  'default-deny'; do
  if command -v rg >/dev/null 2>&1; then
    rg -q -F "$canonical" docs README.md src/melvan/static/app.js || \
      fail "canonical product contract is missing: $canonical"
  else
    grep -RqsF "$canonical" docs README.md src/melvan/static/app.js || \
      fail "canonical product contract is missing: $canonical"
  fi
done

"${PYTHON:-python3}" scripts/check_docs.py || \
  fail "documentation contains broken local links"

if [[ "$status" -eq 0 ]]; then
  printf 'Public-release content checks passed.\n'
fi
exit "$status"
