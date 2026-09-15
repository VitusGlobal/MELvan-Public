#!/usr/bin/env bash

set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

status=0
fail() {
  printf 'FAIL  %s\n' "$1" >&2
  status=1
}

for required in README.md contents.md LICENSE DOCUMENTATION-LICENSE.md SECURITY.md \
  CONTRIBUTING.md CODE_OF_CONDUCT.md \
  THIRD_PARTY_NOTICES.md ASSET-LICENSES.md docs/architecture.md \
  docs/configuration.md docs/azure-deployment.md \
  docs/testing.md docs/security-and-privacy.md docs/principles.md \
  docs/foundational-references.md docs/health-domain-safeguards.md \
  infra/production-foundation.bicep infra/production-migration-job.bicep \
  infra/production-app.bicep scripts/deploy-azure-production.sh; do
  [[ -f "$required" ]] || fail "missing $required"
done

[[ -f LICENSE || -f LICENSE.md || -f LICENSE.txt ]] || \
  fail "no software license has been selected"

if rg -n -i \
  --glob '!scripts/check-publication.sh' \
  --glob '!src/melvan/static/vendor/**' \
  --glob '!.venv*/**' \
  '(sprint[- _]?[0-9]|development notes|acceptance transcript|MartinMBCN|Martin Mills|rg-melvan-demo-canadacentral|melvan\.vitus\.global|/Users/martin)' .; then
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

if rg -n 'PENDING' ASSET-LICENSES.md SECURITY.md CODE_OF_CONDUCT.md; then
  fail "publication governance or asset rights remain unresolved"
fi

for canonical in \
  'results_based_management-gestion_axee_resultats-guide.aspx?lang=eng' \
  'wkkf.issuelab.org/resource/logic-model-development-guide.html' \
  'tired-of-vague-advice-on-writing-a-theory-of-change-narrative-try-the-recap-framework/' \
  'Performance Measurement Framework' \
  'default-deny'; do
  rg -q -F "$canonical" docs README.md src/melvan/static/app.js || \
    fail "canonical product contract is missing: $canonical"
done

"${PYTHON:-python3}" scripts/check_docs.py || \
  fail "documentation contains broken local links"

if [[ "$status" -eq 0 ]]; then
  printf 'Public-release content checks passed.\n'
fi
exit "$status"
