# Changelog

## [0.2.2](https://github.com/Rubio-Enterprises/pi-sandbox/compare/v0.2.1...v0.2.2) (2026-11-13)


### Documentation

* **adr:** convert records to the minimal format ([#37](https://github.com/Rubio-Enterprises/pi-sandbox/issues/37)) ([6421c8e](https://github.com/Rubio-Enterprises/pi-sandbox/commit/6421c8e782280b8294349eea134426b5d68f293d))
* name the spec:agent state in the adr rule ([#39](https://github.com/Rubio-Enterprises/pi-sandbox/issues/39)) ([864af32](https://github.com/Rubio-Enterprises/pi-sandbox/commit/864af32cb0712d160e1598888366adfa24ea2d95))

## [0.2.1](https://github.com/Rubio-Enterprises/pi-sandbox/compare/v0.2.0...v0.2.1) (2026-11-10)


### Bug Fixes

* resolve pi past inactive mise shims ([#34](https://github.com/Rubio-Enterprises/pi-sandbox/issues/34)) ([79e5a53](https://github.com/Rubio-Enterprises/pi-sandbox/commit/79e5a531c80e99e79ce227e0a8dff804a3b61838))

## [0.2.0](https://github.com/Rubio-Enterprises/pi-sandbox/compare/v0.1.3...v0.2.0) (2026-11-09)


### ⚠ BREAKING CHANGES

* replace numbered TOML layers and fragments with native nono JSON Sandbox Profiles and optional Pi sidecars. Select with --profile or PI_SANDBOX_PROFILE; migrate static grants, environment rules and macOS port rules into native JSON. The controller layers command is replaced by chain.

### Features

* compose sandbox policy from native nono profiles ([c1d6c10](https://github.com/Rubio-Enterprises/pi-sandbox/commit/c1d6c102258d8f658ae1ba23268c9d0a8be7a115))


### Bug Fixes

* **ctl:** show complete sandbox composition previews ([2bd8adb](https://github.com/Rubio-Enterprises/pi-sandbox/commit/2bd8adb65aa455bfd3240acf41ab789e5c81835f))
* keep service credential paths in sandbox profiles ([593b601](https://github.com/Rubio-Enterprises/pi-sandbox/commit/593b601bec7f8a18a90d862d28f276d4186c2bef))


### Documentation

* document sandbox profiles and pi sidecar migration ([2178042](https://github.com/Rubio-Enterprises/pi-sandbox/commit/217804245a8efc97a056ef3e8b87abd187d9ff87))
* explain why herdr cannot show blocked inside the sandbox ([#31](https://github.com/Rubio-Enterprises/pi-sandbox/issues/31)) ([a383e49](https://github.com/Rubio-Enterprises/pi-sandbox/commit/a383e49f791f01d82177b6cd20758833ce966231))

## [0.1.3](https://github.com/Rubio-Enterprises/pi-sandbox/compare/v0.1.2...v0.1.3) (2026-11-08)


### Features

* give sandboxed builds their own ghcr.io token ([#30](https://github.com/Rubio-Enterprises/pi-sandbox/issues/30)) ([06dc8bf](https://github.com/Rubio-Enterprises/pi-sandbox/commit/06dc8bfa5b9e573c3be020e8b09d1da741a8e4bf))


### Bug Fixes

* **python:** make uv use the mise-pinned python ([#23](https://github.com/Rubio-Enterprises/pi-sandbox/issues/23)) ([ef3d8f0](https://github.com/Rubio-Enterprises/pi-sandbox/commit/ef3d8f0034449a9a54521483147ef1537d5a97ed))

## [0.1.2](https://github.com/Rubio-Enterprises/pi-sandbox/compare/v0.1.1...v0.1.2) (2026-10-31)


### Features

* hide mcp tools by logical server ([#11](https://github.com/Rubio-Enterprises/pi-sandbox/issues/11)) ([674a86b](https://github.com/Rubio-Enterprises/pi-sandbox/commit/674a86b28de00a2db71de008e78d4a00cb6495dd))

## [0.1.1](https://github.com/Rubio-Enterprises/pi-sandbox/compare/v0.1.0...v0.1.1) (2026-10-31)


### Features

* protect pi ai-accounts configuration from sandboxed writes ([#5](https://github.com/Rubio-Enterprises/pi-sandbox/issues/5)) ([1b90cf3](https://github.com/Rubio-Enterprises/pi-sandbox/commit/1b90cf33f47fc6fe77e8b1c83e73921b6b0a63bc))


### Bug Fixes

* **ci:** satisfy native container and test contracts ([#6](https://github.com/Rubio-Enterprises/pi-sandbox/issues/6)) ([e8421e2](https://github.com/Rubio-Enterprises/pi-sandbox/commit/e8421e25898fec189568dddb9ece6f996b5b361b))

## 0.1.0 (2026-10-31)


### Features

* add the layered pi-sandbox engine ([#1](https://github.com/Rubio-Enterprises/pi-sandbox/issues/1)) ([71f7c5e](https://github.com/Rubio-Enterprises/pi-sandbox/commit/71f7c5ebdba7546f26021a047543d2dbb12c2093))
