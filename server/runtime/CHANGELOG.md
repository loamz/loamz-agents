# Changelog

## [0.1.1](https://github.com/loamz/loamz-agents/compare/loamz-runtime-v0.1.0...loamz-runtime-v0.1.1) (2026-10-31)


### Features

* **agents:** a data_chart tool that draws a query result as a chart ([81b6d7f](https://github.com/loamz/loamz-agents/commit/81b6d7fd01f6e3b60c56a663d63c8459237a5cb9))
* **agents:** data tools (search, describe, SQL) that query as the signed-in person and their teams ([1f1fa6d](https://github.com/loamz/loamz-agents/commit/1f1fa6de9d6a7afeca1a75d3885dcd7adf5d9143))
* **agents:** hosted agents can use the code interpreter, with governed data ([034259f](https://github.com/loamz/loamz-agents/commit/034259f3bf4b44ab916d75a9d7f1bd60d7d9fff0))
* **agents:** the sandbox holds no data credential; one gateway socket per run, with the run's person and teams, and a byte budget ([ac4fd27](https://github.com/loamz/loamz-agents/commit/ac4fd27cf620636fce4f632c43ceea5a22381064))
* **agents:** tool servers (MCP) for agents, with per-agent tool lists and durable approval of risky calls ([33408b8](https://github.com/loamz/loamz-agents/commit/33408b864c637ffa000c45a5055b917e60388d35))
* **console:** tools and agents can draw UI in a message, in the json-render standard ([f40db60](https://github.com/loamz/loamz-agents/commit/f40db602f24aecf8e7ff847276d910650e3ea25d))
* implement skill management system with upload and parsing capabilities ([17846ba](https://github.com/loamz/loamz-agents/commit/17846baf8a339d024178441eabe8f884e3223d20))
* **messenger:** people and agents in shared chats, a live feed, push and a nonce CSP (Phase 0, first part) ([51b1053](https://github.com/loamz/loamz-agents/commit/51b10539827d1b4d33751fb322dc1de6b4bee34a))
* **messenger:** typing, receipts, sealing at rest, an XSS corpus, and verified two-user chat (Phase 0 and S, second part) ([6a13dc5](https://github.com/loamz/loamz-agents/commit/6a13dc53c210167195996aeb2d2ab8fa6e13b84e))
* one bubble per turn, A2UI in groups, and web pictures through a safe proxy ([52aaf9b](https://github.com/loamz/loamz-agents/commit/52aaf9b183b38b8c386e66298079066245beb94b))


### Bug Fixes

* a message shows its chart as it was, even after the agent redraws the file ([2964ad0](https://github.com/loamz/loamz-agents/commit/2964ad096a5bde47c11394d78964572863636ba9))
* files the agent makes show in the chat; a restart no longer blocks a conversation; no repeated sender name ([859c8e1](https://github.com/loamz/loamz-agents/commit/859c8e1d0b177f0bdb481de22f65c0d3f62e4c94))
* **sandbox:** code never runs as root; each run's data socket belongs to the id the code runs as ([538e3ba](https://github.com/loamz/loamz-agents/commit/538e3ba5e5157340e36afd6ab0d3cbbf6221f0fd))
* **tools:** a tool server connection is opened and closed by one owner task; dead servers fail fast and cannot starve or cancel other work ([6d78c68](https://github.com/loamz/loamz-agents/commit/6d78c6851e524f37eeb01d79f7aa329008e548ef))
