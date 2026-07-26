# Third-party notices

This file covers third-party source copied into this repository and the direct
production dependencies of the web console. Transitive dependency licenses
remain in their distributed packages and are checked from the lockfile install
in CI.

## Copied shadcn/ui components

Files under `apps/web-console/components/ui/` are adapted from shadcn/ui
components and include project modifications.

MIT License

Copyright (c) 2023 shadcn

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
of the Software, and to permit persons to whom the Software is furnished to do
so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

Source: <https://github.com/shadcn-ui/ui>

## Direct web dependencies

Versions are resolved by `pnpm-lock.yaml`; the ranges remain in
`apps/web-console/package.json`.

| Package | License | Upstream |
|---|---|---|
| `@radix-ui/react-dialog` | MIT | <https://github.com/radix-ui/primitives> |
| `@radix-ui/react-label` | MIT | <https://github.com/radix-ui/primitives> |
| `@radix-ui/react-select` | MIT | <https://github.com/radix-ui/primitives> |
| `@radix-ui/react-slot` | MIT | <https://github.com/radix-ui/primitives> |
| `@radix-ui/react-tabs` | MIT | <https://github.com/radix-ui/primitives> |
| `@radix-ui/react-tooltip` | MIT | <https://github.com/radix-ui/primitives> |
| `class-variance-authority` | Apache-2.0 | <https://github.com/joe-bell/cva> |
| `clsx` | MIT | <https://github.com/lukeed/clsx> |
| `lucide-react` | ISC, with Feather portions under MIT | <https://github.com/lucide-icons/lucide> |
| `next` | MIT | <https://github.com/vercel/next.js> |
| `next-themes` | MIT | <https://github.com/pacocoursey/next-themes> |
| `react` | MIT | <https://github.com/facebook/react> |
| `react-dom` | MIT | <https://github.com/facebook/react> |
| `tailwind-merge` | MIT | <https://github.com/dcastil/tailwind-merge> |
| `tw-animate-css` | MIT | <https://github.com/Wombosvideo/tw-animate-css> |

## Twilio deployment tooling

| Package | License | Upstream |
|---|---|---|
| `twilio-run` | MIT | <https://github.com/twilio-labs/serverless-toolkit> |

The locked `twilio-run` graph is local deployment tooling, not code bundled
into the deployed Function. Its Twilio Labs components use the MIT License:

Copyright (c) 2019 Twilio Labs

The MIT permission and warranty text is printed in the copied shadcn/ui
section above.

## Reviewed build-only binary dependency

Next.js may install a platform-specific `@img/sharp-libvips-*` package under
LGPL-3.0-or-later for build-time image processing. The final web artifact is a
static export served by nginx and contains neither `node_modules`, Sharp, nor
libvips. The recursive license checker recognizes only that exact build-only
package family as a reviewed exception; another restrictive dependency fails
the check.

### MIT dependency notices

- Copyright (c) 2022 WorkOS — Radix UI Primitives
- Copyright (c) Luke Edwards — clsx
- Copyright (c) 2025 Vercel, Inc. — Next.js
- Copyright (c) 2022 Paco Coursey — next-themes
- Copyright (c) Meta Platforms, Inc. and affiliates — React and React DOM
- Copyright (c) 2021 Dany Castillo — tailwind-merge
- Copyright (c) 2025 Wombosvideo — tw-animate-css
- Copyright (c) 2019 Twilio Labs — Twilio Serverless Toolkit

Each work above is provided under the MIT permission and warranty text printed
in the shadcn/ui section of this file, with its own copyright notice retained.

### class-variance-authority

Copyright 2022 Joe Bell

Licensed under the Apache License, Version 2.0. A complete copy appears in the
repository's `LICENSE` file.

### Lucide and Feather

ISC License

Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2023 as part
of Feather (MIT). All other copyright (c) for Lucide are held by Lucide
Contributors 2025.

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR
OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
PERFORMANCE OF THIS SOFTWARE.

Feather-derived portions:

MIT License

Copyright (c) 2013-present Cole Bemis

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
of the Software, and to permit persons to whom the Software is furnished to do
so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
