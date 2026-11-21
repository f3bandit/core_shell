# Third-party work in Coreshell

Coreshell itself is Copyright (c) 2026 f3bandit, under the Apache License 2.0 ([LICENSE](LICENSE), [NOTICE](NOTICE)).
These are the other people's works it contains or is built with, and their licences.

| Work | Authors | Licence | Where, and how it's used |
|---|---|---|---|
| [nextpnr](https://github.com/YosysHQ/nextpnr) | YosysHQ and contributors | ISC | `corepatch/nextpnr-base.patch` is a change to nextpnr (at the revision in `corepatch/nextpnr-base.rev`), under nextpnr's licence. nextpnr itself isn't included. |
| [Mistral](https://github.com/Ravenslofty/mistral) | Olivier Galibert and contributors | BSD 3-Clause | The PC tools in `corepatch/tools/` are built against it. It isn't included. |
| Mistery | meathax | BSD 3-Clause | Used to study stock cores. It isn't included. |
| Core menus (`dumps/`, `systems/`) | Each core's authors (most MiSTer cores: Alexey Melnikov "Sorgelig" and the MiSTer devs) | Each core's own licence (most are GPL) | Each core's menu text (its `CONF_STR`), word for word, as data Coreshell reads. Each file names the core and build it came from. Never compiled or linked into Coreshell, and not covered by Coreshell's licence. |

Coreshell starts, but doesn't include, change or link with, Main_MiSTer and its forks (MiSTer_RA by odelot,
MiSTer_Physical-CD by Anime0t4ku and bonna79, and the others credited in `mains.json`), the MiSTer cores, and
the cores Update All installs. Each is under its own licence, by its own authors. Update All's databases are
fetched at run time and not included.

Coreshell is not part of, affiliated with or endorsed by the MiSTer project (MiSTer-devel) or any of the authors
above.
