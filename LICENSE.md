BSD 3-Clause License

Copyright (c) 2026, The Camouwolf Authors
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software
   without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

---

## Third-party components

**Chromium** — the `browser/patches/` series is a delta against the Chromium
source, which is itself BSD-3-Clause with additional licenses for bundled
components. See the upstream `LICENSE` and `LICENSES/` in a Chromium checkout.
Camouwolf's patches are licensed to match so the combined work stays coherent.

**Bundled fonts** (`browser/bundle/fonts/`, not committed) retain their own
licenses. They are cross-OS fingerprinting inputs, not redistributable assets;
see `browser/bundle/fonts/000_README.txt`.

**Font and voice manifests** (`src/mappings/fonts-manifests.ts`,
`src/mappings/voices-manifests.ts`) are shared with Holo, which is MPL-2.0.
They describe operating-system inventories rather than browser code and are
carried here under the same terms.
