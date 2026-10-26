# net toolkit

Network tools. A skill uses them by declaring `toolkits: [net]` in its `skill.yaml`; their
contracts live only in this toolkit's `tools.yaml`. All of them are internal (the model never
sees them) unless a skill lists one under `expose:`.

## System Requirements

None beyond the Python core's `requests`. No credentials, no proxy configuration of its own
(the standard `HTTP(S)_PROXY` variables apply).

## Tools

| Tool | Kind | What it does |
|---|---|---|
| `download_file` | Handler | Downloads one http(s) URL into a folder of the run scratch, under the URL's file name (`knaif.urls.url_file_name`). Returns `{path, bytes, content_type, url, final_url}`. In a dry run it makes no request and returns the path it would write. |

## Policy

Network access is new for knaif, so the policy lives in this one tool:

- **Only http and https, no credentials.** Another scheme, or a URL with a user name or password,
  is refused before any request and on every redirect. At most 5 redirects.
- **No cookies, a named user agent** (`knaif/<version>`).
- **Timeouts.** 10 s to connect and 60 s per read, both settable by the caller, and 10 min
  overall.
- **The skill's content types.** A response outside the skill's `accepted` list is refused. A web
  page is named as one, since a link to the page that plays a video is the usual mistake.
- **A size cap.** A `Content-Length` over `max_bytes` is refused before the body. A body that
  passes the cap while streaming is cut off.
- **Nothing left behind.** A refusal leaves no file. The bytes go to `<name>.part` and are renamed
  only when complete, and only inside the run scratch.

Asking the user is not this tool's job. The pipeline asks once per URL, or requires
`--allow-download` when there is no terminal, before it calls `download_file`.

The URL rules themselves (`knaif.urls`: the value class, the refusals, naming, content types)
are pinned for both runtimes by `contracts/parity/url_inputs_cases.json`.

**Tests and evals** use `knaif.evalsuite.url_server.UrlServer`, a local server with routes that
misbehave on purpose. `KNAIF_NET_TEST_HOST=files.knaif.test=<base>` sends the reserved test host
(RFC 6761) to it; the product never sets it.

## Native

The `knaif-toolkit-net` crate (`native/`) is the same policy in Rust: `download_file(url,
dest_dir, &Policy)` on ureq (redirects followed by hand, so every hop is checked), and
`planned_path` for a dry run. A timeout is recognised by its I/O error kind, since Windows
reports a read timeout as os error 10060, whose text never says "timed out". Its tests run
against their own local server (`tests/download.rs`).

## Effects

`download_file` declares `[net, fs.write]`, so a step that calls it through `ctx.call_tool` must
declare both.

## Used by

`ffmpeg`, `documents` (`url_inputs:` in their `skill.yaml`; see *URL inputs* in `docs/TOOL_SCHEMA.md`).
