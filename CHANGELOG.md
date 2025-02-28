# CHANGELOG

## v0.8.0 (2025-02-28)

### Chores

- Formatting
  ([`93cc637`](https://github.com/basicmachines-co/basic-memory/commit/93cc6379ebb9ecc6a1652feeeecbf47fc992d478))

- Refactor logging setup
  ([`f4b703e`](https://github.com/basicmachines-co/basic-memory/commit/f4b703e57f0ddf686de6840ff346b8be2be499ad))

### Features

- Add enhanced prompts and resources
  ([#15](https://github.com/basicmachines-co/basic-memory/pull/15),
  [`093dab5`](https://github.com/basicmachines-co/basic-memory/commit/093dab5f03cf7b090a9f4003c55507859bf355b0))
- `search`: Prompt to search knowledge base
- `recent_activity`: Prompt to find recent activity
- `continue_conversation`: Prompt to continue a conversation based on contect
- `ai_assistant_guide`: Resource with description to give to LLM to understand how to use the tools

---------

Co-authored-by: phernandez <phernandez@basicmachines.co>

- Add new `canvas` tool to create json canvas files in obsidian.
  ([#14](https://github.com/basicmachines-co/basic-memory/pull/14),
  [`0d7b0b3`](https://github.com/basicmachines-co/basic-memory/commit/0d7b0b3d7ede7555450ddc9728951d4b1edbbb80))

Add new `canvas` tool to create json canvas files in obsidian.

---------

Co-authored-by: phernandez <phernandez@basicmachines.co>

- Incremental sync on watch ([#13](https://github.com/basicmachines-co/basic-memory/pull/13),
  [`37a01b8`](https://github.com/basicmachines-co/basic-memory/commit/37a01b806d0758029d34a862e76d44c7e5d538a5))

- incremental sync on watch - sync non-markdown files in knowledge base - experimental
  `read_resource` tool for reading non-markdown files in raw form (pdf, image)

## v0.7.0 (2025-02-19)

### Bug Fixes

- Add logfire instrumentation to tools
  ([`3e8e3e8`](https://github.com/basicmachines-co/basic-memory/commit/3e8e3e8961eae2e82839746e28963191b0aef0a0))

- Add logfire spans to cli
  ([`00d23a5`](https://github.com/basicmachines-co/basic-memory/commit/00d23a5ee15ddac4ea45e702dcd02ab9f0509276))

- Add logfire spans to cli
  ([`812136c`](https://github.com/basicmachines-co/basic-memory/commit/812136c8c22ad191d14ff32dcad91aae076d4120))

- Search query pagination params
  ([`bc9ca07`](https://github.com/basicmachines-co/basic-memory/commit/bc9ca0744ffe4296d7d597b4dd9b7c73c2d63f3f))

### Chores

- Fix tests
  ([`57984aa`](https://github.com/basicmachines-co/basic-memory/commit/57984aa912625dcde7877afb96d874c164af2896))

- Remove unused tests
  ([`2c8ed17`](https://github.com/basicmachines-co/basic-memory/commit/2c8ed1737d6769fe1ef5c96f8a2bd75b9899316a))

### Features

- Add cli commands for mcp tools
  ([`f5a7541`](https://github.com/basicmachines-co/basic-memory/commit/f5a7541da17e97403b7a702720a05710f68b223a))

- Add pagination to build_context and recent_activity
  ([`0123544`](https://github.com/basicmachines-co/basic-memory/commit/0123544556513af943d399d70b849b142b834b15))

- Add pagination to read_notes
  ([`02f8e86`](https://github.com/basicmachines-co/basic-memory/commit/02f8e866923d5793d2620076c709c920d99f2c4f))

## v0.6.0 (2025-02-18)

### Chores

- Re-add sync status console on watch
  ([`66b57e6`](https://github.com/basicmachines-co/basic-memory/commit/66b57e682f2e9c432bffd4af293b0d1db1d3469b))

### Features

- Configure logfire telemetry ([#12](https://github.com/basicmachines-co/basic-memory/pull/12),
  [`6da1438`](https://github.com/basicmachines-co/basic-memory/commit/6da143898bd45cdab8db95b5f2b75810fbb741ba))

Co-authored-by: phernandez <phernandez@basicmachines.co>

## v0.5.0 (2025-02-18)

### Features

- Return semantic info in markdown after write_note
  ([#11](https://github.com/basicmachines-co/basic-memory/pull/11),
  [`0689e7a`](https://github.com/basicmachines-co/basic-memory/commit/0689e7a730497827bf4e16156ae402ddc5949077))

Co-authored-by: phernandez <phernandez@basicmachines.co>

## v0.4.3 (2025-02-18)

### Bug Fixes

- Re do enhanced read note format ([#10](https://github.com/basicmachines-co/basic-memory/pull/10),
  [`39bd5ca`](https://github.com/basicmachines-co/basic-memory/commit/39bd5ca08fd057220b95a8b5d82c5e73a1f5722b))

Co-authored-by: phernandez <phernandez@basicmachines.co>

## v0.4.2 (2025-02-17)

## v0.4.1 (2025-02-17)

### Bug Fixes

- Fix alemic config
  ([`71de8ac`](https://github.com/basicmachines-co/basic-memory/commit/71de8acfd0902fc60f27deb3638236a3875787ab))

- More alembic fixes
  ([`30cd74e`](https://github.com/basicmachines-co/basic-memory/commit/30cd74ec95c04eaa92b41b9815431f5fbdb46ef8))

## v0.4.0 (2025-02-16)

### Features

- Import chatgpt conversation data ([#9](https://github.com/basicmachines-co/basic-memory/pull/9),
  [`56f47d6`](https://github.com/basicmachines-co/basic-memory/commit/56f47d6812982437f207629e6ac9a82e0e56514e))

Co-authored-by: phernandez <phernandez@basicmachines.co>

- Import claude.ai data ([#8](https://github.com/basicmachines-co/basic-memory/pull/8),
  [`a15c346`](https://github.com/basicmachines-co/basic-memory/commit/a15c346d5ebd44344b76bad877bb4d1073fcbc3b))

Import Claude.ai conversation and project data to basic-memory Markdown format.

---------

Co-authored-by: phernandez <phernandez@basicmachines.co>

## v0.3.0 (2025-02-15)

### Bug Fixes

- Refactor db schema migrate handling
  ([`ca632be`](https://github.com/basicmachines-co/basic-memory/commit/ca632beb6fed5881f4d8ba5ce698bb5bc681e6aa))

## v0.2.21 (2025-02-15)

### Bug Fixes

- Fix osx installer github action
  ([`65ebe5d`](https://github.com/basicmachines-co/basic-memory/commit/65ebe5d19491e5ff047c459d799498ad5dd9cd1a))

- Handle memory:// url format in read_note tool
  ([`e080373`](https://github.com/basicmachines-co/basic-memory/commit/e0803734e69eeb6c6d7432eea323c7a264cb8347))

- Remove create schema from init_db
  ([`674dd1f`](https://github.com/basicmachines-co/basic-memory/commit/674dd1fd47be9e60ac17508476c62254991df288))

### Features

- Set version in var, output version at startup
  ([`a91da13`](https://github.com/basicmachines-co/basic-memory/commit/a91da1396710e62587df1284da00137d156fc05e))

## v0.2.20 (2025-02-14)

### Bug Fixes

- Fix installer artifact
  ([`8de84c0`](https://github.com/basicmachines-co/basic-memory/commit/8de84c0221a1ee32780aa84dac4d3ea60895e05c))

## v0.2.19 (2025-02-14)

### Bug Fixes

- Get app artifact for installer
  ([`fe8c3d8`](https://github.com/basicmachines-co/basic-memory/commit/fe8c3d87b003166252290a87cbe958301cccf797))

## v0.2.18 (2025-02-14)

### Bug Fixes

- Don't zip app on release
  ([`8664c57`](https://github.com/basicmachines-co/basic-memory/commit/8664c57bb331d7f3f7e0239acb5386c7a3c6144e))

## v0.2.17 (2025-02-14)

### Bug Fixes

- Fix app zip in installer release
  ([`8fa197e`](https://github.com/basicmachines-co/basic-memory/commit/8fa197e2ec8a1b6caaf6dbb39c3c6626bba23e2e))

## v0.2.16 (2025-02-14)

### Bug Fixes

- Debug inspect build on ci
  ([`1d6054d`](https://github.com/basicmachines-co/basic-memory/commit/1d6054d30a477a4e6a5d6ac885632e50c01945d3))

## v0.2.15 (2025-02-14)

### Bug Fixes

- Debug installer ci
  ([`dab9573`](https://github.com/basicmachines-co/basic-memory/commit/dab957314aec9ed0e12abca2265552494ae733a2))

## v0.2.14 (2025-02-14)

## v0.2.13 (2025-02-14)

### Bug Fixes

- Refactor release.yml installer
  ([`a152657`](https://github.com/basicmachines-co/basic-memory/commit/a15265783e47c22d8c7931396281d023b3694e27))

- Try using symlinks in installer build
  ([`8dd923d`](https://github.com/basicmachines-co/basic-memory/commit/8dd923d5bc0587276f92b5f1db022ad9c8687e45))

## v0.2.12 (2025-02-14)

### Bug Fixes

- Fix cx_freeze options for installer
  ([`854cf83`](https://github.com/basicmachines-co/basic-memory/commit/854cf8302e2f83578030db05e29b8bdc4348795a))

## v0.2.11 (2025-02-14)

### Bug Fixes

- Ci installer app fix #37
  ([`2e215fe`](https://github.com/basicmachines-co/basic-memory/commit/2e215fe83ca421b921186c7f1989dc2cb5cca278))

## v0.2.10 (2025-02-14)

### Bug Fixes

- Fix build on github ci for app installer
  ([`29a2594`](https://github.com/basicmachines-co/basic-memory/commit/29a259421a0ccb10cfa68e3707eaa506ad5e55c0))

## v0.2.9 (2025-02-14)

## v0.2.8 (2025-02-14)

### Bug Fixes

- Fix installer on ci, maybe
  ([`edbc04b`](https://github.com/basicmachines-co/basic-memory/commit/edbc04be601d234bb1f5eb3ba24d6ad55244b031))

## v0.2.7 (2025-02-14)

### Bug Fixes

- Try to fix installer ci
  ([`230738e`](https://github.com/basicmachines-co/basic-memory/commit/230738ee9c110c0509e0a09cb0e101a92cfcb729))

## v0.2.6 (2025-02-14)

### Bug Fixes

- Bump project patch version
  ([`01d4672`](https://github.com/basicmachines-co/basic-memory/commit/01d46727b40c24b017ea9db4b741daef565ac73e))

- Fix installer setup.py change ci to use make
  ([`3e78fcc`](https://github.com/basicmachines-co/basic-memory/commit/3e78fcc2c208d83467fe7199be17174d7ffcad1a))

## v0.2.5 (2025-02-14)

### Bug Fixes

- Refix vitual env in installer build
  ([`052f491`](https://github.com/basicmachines-co/basic-memory/commit/052f491fff629e8ead629c9259f8cb46c608d584))

## v0.2.4 (2025-02-14)

## v0.2.3 (2025-02-14)

### Bug Fixes

- Workaround unsigned app
  ([`41d4d81`](https://github.com/basicmachines-co/basic-memory/commit/41d4d81c1ad1dc2923ba0e903a57454a0c8b6b5c))

## v0.2.2 (2025-02-14)

### Bug Fixes

- Fix path to intaller app artifact
  ([`53d220d`](https://github.com/basicmachines-co/basic-memory/commit/53d220df585561f9edd0d49a9e88f1d4055059cf))

## v0.2.1 (2025-02-14)

### Bug Fixes

- Activate vitualenv in installer build
  ([`d4c8293`](https://github.com/basicmachines-co/basic-memory/commit/d4c8293687a52eaf3337fe02e2f7b80e4cc9a1bb))

- Trigger installer build on release
  ([`f11bf78`](https://github.com/basicmachines-co/basic-memory/commit/f11bf78f3f600d0e1b01996cf8e1f9c39e3dd218))

## v0.2.0 (2025-02-14)

### Features

- Build installer via github action ([#7](https://github.com/basicmachines-co/basic-memory/pull/7),
  [`7c381a5`](https://github.com/basicmachines-co/basic-memory/commit/7c381a59c962053c78da096172e484f28ab47e96))

* feat(ci): build installer via github action

* enforce conventional commits in PR titles

* feat: add icon to installer

---------

Co-authored-by: phernandez <phernandez@basicmachines.co>

## v0.1.2 (2025-02-14)

### Bug Fixes

- Fix installer for mac
  ([`dde9ff2`](https://github.com/basicmachines-co/basic-memory/commit/dde9ff228b72852b5abc58faa1b5e7c6f8d2c477))

- Remove unused FileChange dataclass
  ([`eb3360c`](https://github.com/basicmachines-co/basic-memory/commit/eb3360cc221f892b12a17137ae740819d48248e8))

- Update uv installer url
  ([`2f9178b`](https://github.com/basicmachines-co/basic-memory/commit/2f9178b0507b3b69207d5c80799f2d2f573c9a04))

## v0.1.1 (2025-02-07)

## v0.1.0 (2025-02-07)

### Bug Fixes

- Create virtual env in test workflow
  ([`8092e6d`](https://github.com/basicmachines-co/basic-memory/commit/8092e6d38d536bfb6f93c3d21ea9baf1814f9b0a))

- Fix permalink uniqueness violations on create/update/sync
  ([`135bec1`](https://github.com/basicmachines-co/basic-memory/commit/135bec181d9b3d53725c8af3a0959ebc1aa6afda))

- Fix recent activity bug
  ([`3d2c0c8`](https://github.com/basicmachines-co/basic-memory/commit/3d2c0c8c32fcfdaf70a1f96a59d8f168f38a1aa9))

- Install fastapi deps after removing basic-foundation
  ([`51a741e`](https://github.com/basicmachines-co/basic-memory/commit/51a741e7593a1ea0e5eb24e14c70ff61670f9663))

- Recreate search index on db reset
  ([`1fee436`](https://github.com/basicmachines-co/basic-memory/commit/1fee436bf903a35c9ebb7d87607fc9cc9f5ff6e7))

- Remove basic-foundation from deps
  ([`b8d0c71`](https://github.com/basicmachines-co/basic-memory/commit/b8d0c7160f29c97cdafe398a7e6a5240473e0c89))

- Run tests via uv
  ([`4eec820`](https://github.com/basicmachines-co/basic-memory/commit/4eec820a32bc059a405e2f4dac4c73b245ca4722))

### Chores

- Rename import tool
  ([`af6b7dc`](https://github.com/basicmachines-co/basic-memory/commit/af6b7dc40a55eaa2aa78d6ea831e613851081d52))

### Features

- Add memory-json importer, tweak observation content
  ([`3484e26`](https://github.com/basicmachines-co/basic-memory/commit/3484e26631187f165ee6eb85517e94717b7cf2cf))

## v0.0.1 (2025-02-04)

### Bug Fixes

- Fix versioning for 0.0.1 release
  ([`ba1e494`](https://github.com/basicmachines-co/basic-memory/commit/ba1e494ed1afbb7af3f97c643126bced425da7e0))

## v0.0.0 (2025-02-04)

### Chores

- Remove basic-foundation src ref in pyproject.toml
  ([`29fce8b`](https://github.com/basicmachines-co/basic-memory/commit/29fce8b0b922d54d7799bf2534107ee6cfb961b8))