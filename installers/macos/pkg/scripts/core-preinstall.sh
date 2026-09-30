#!/bin/bash
# preinstall of tech.blackdeep.knaif.core — installed as `preinstall` by build-pkg.sh.
#
# Clear the previous install's program folders before the new payload lands. A package only ever
# adds or replaces files, so without this an upgrade keeps every file the new version dropped, and
# a skill deselected on upgrade stays installed. The same folders knaif.iss's [InstallDelete]
# clears on Windows; user data lives in ~/.knaif and is never touched.

# shellcheck source=common.sh
. "$(dirname "$0")/common.sh"

for dir in bin skills contracts licenses; do
  rm -rf "${KNAIF_ROOT:?}/$dir"
done
exit 0
