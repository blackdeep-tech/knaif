//! The native download_file against a local server: the same policy cases as the Python tests
//! (toolkits/net/python/tests/test_download_file.py). A refused URL makes no request, every
//! redirect is held to the rules, and a refusal leaves nothing behind.

use std::io::{BufRead, BufReader, Write};
use std::net::{TcpListener, TcpStream};
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use knaif_toolkit_net::{download_file, Policy};

/// Every request the server saw: its path and its lower-cased header lines.
type Log = Arc<Mutex<Vec<(String, Vec<String>)>>>;

const CLIP: &[u8] = b"\x00\x00\x00\x18ftypmp42xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx";

struct Server {
    base: String,
    requests: Log,
}

fn respond(mut s: TcpStream, status: u16, headers: &[(&str, String)], body: &[u8]) {
    let mut head = format!("HTTP/1.0 {status} X\r\n");
    for (k, v) in headers {
        head.push_str(&format!("{k}: {v}\r\n"));
    }
    head.push_str("\r\n");
    let _ = s.write_all(head.as_bytes());
    let _ = s.write_all(body);
}

fn serve() -> Server {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let requests: Log = Arc::default();
    let log = requests.clone();
    thread::spawn(move || {
        for stream in listener.incoming().flatten() {
            let log = log.clone();
            thread::spawn(move || {
                let mut reader = BufReader::new(stream.try_clone().unwrap());
                let mut line = String::new();
                if reader.read_line(&mut line).is_err() {
                    return;
                }
                let path = line.split_whitespace().nth(1).unwrap_or("/").to_string();
                let mut headers = vec![];
                loop {
                    let mut h = String::new();
                    if reader.read_line(&mut h).is_err() || h.trim().is_empty() {
                        break;
                    }
                    headers.push(h.trim().to_lowercase());
                }
                log.lock().unwrap().push((path.clone(), headers));
                route(stream, &path);
            });
        }
    });
    Server { base, requests }
}

fn route(s: TcpStream, path: &str) {
    let (path, query) = path.split_once('?').unwrap_or((path, ""));
    if let Some(rest) = path.strip_prefix("/_/redirect/") {
        let (n, tail) = rest.split_once('/').unwrap();
        let n: u32 = n.parse().unwrap();
        let to = if n > 1 {
            format!("/_/redirect/{}/{tail}", n - 1)
        } else {
            format!("/{tail}")
        };
        return respond(s, 302, &[("Location", to)], b"");
    }
    if path == "/_/redirect-to" {
        let to = query.strip_prefix("location=").unwrap().to_string();
        return respond(s, 302, &[("Location", to)], b"");
    }
    if let Some(n) = path.strip_prefix("/_/big/") {
        let n: usize = n.parse().unwrap();
        let mut h = vec![("Content-Type", "application/octet-stream".to_string())];
        if query != "nolen=1" {
            h.push(("Content-Length", n.to_string()));
        }
        return respond(s, 200, &h, &vec![0u8; n]);
    }
    if let Some(secs) = path.strip_prefix("/_/slow/") {
        thread::sleep(Duration::from_secs_f64(secs.parse().unwrap()));
        return respond(
            s,
            200,
            &[("Content-Type", "application/octet-stream".into())],
            b"late",
        );
    }
    if path == "/_/html" {
        return respond(
            s,
            200,
            &[("Content-Type", "text/html; charset=utf-8".into())],
            b"<html/>",
        );
    }
    match path {
        "/media/holiday.mp4" => respond(
            s,
            200,
            &[
                ("Content-Type", "video/mp4".into()),
                ("Content-Length", CLIP.len().to_string()),
            ],
            CLIP,
        ),
        "/docs/report.pdf" => respond(
            s,
            200,
            &[("Content-Type", "application/pdf".into())],
            b"%PDF-1.4",
        ),
        _ => respond(s, 404, &[], b"not found"),
    }
}

fn policy() -> Policy {
    Policy::new(
        vec!["video/*".into(), "application/octet-stream".into()],
        1_000_000,
    )
}

fn left(dest: &Path) -> Vec<String> {
    std::fs::read_dir(dest)
        .map(|d| {
            d.flatten()
                .map(|e| e.file_name().to_string_lossy().into_owned())
                .collect()
        })
        .unwrap_or_default()
}

fn tmp(name: &str) -> std::path::PathBuf {
    let d = std::env::temp_dir().join(format!("knaif-net-test-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    d
}

#[test]
fn a_url_lands_under_its_own_name() {
    let s = serve();
    let dest = tmp("lands");
    let got = download_file(&format!("{}/media/holiday.mp4", s.base), &dest, &policy()).unwrap();
    assert_eq!(got.path, dest.join("holiday.mp4"));
    assert_eq!(std::fs::read(&got.path).unwrap(), CLIP);
    assert_eq!(got.content_type, "video/mp4");
    let headers = &s.requests.lock().unwrap()[0].1;
    assert!(headers.iter().any(|h| h.starts_with("user-agent: knaif/")));
    assert!(!headers
        .iter()
        .any(|h| h.starts_with("cookie") || h.starts_with("authorization")));
}

#[test]
fn redirects_are_followed_and_a_taken_name_gets_a_free_one() {
    let s = serve();
    let dest = tmp("redir");
    let url = format!("{}/_/redirect/3/media/holiday.mp4", s.base);
    let first = download_file(&url, &dest, &policy()).unwrap();
    assert_eq!(first.final_url, format!("{}/media/holiday.mp4", s.base));
    let second = download_file(&url, &dest, &policy()).unwrap();
    assert_eq!(second.path.file_name().unwrap(), "holiday-1.mp4");
}

#[test]
fn a_refused_url_makes_no_request() {
    let s = serve();
    let dest = tmp("refused");
    for (url, phrase) in [
        ("ftp://127.0.0.1/a.mp4".to_string(), "only http and https"),
        (
            format!("http://alice:secret@{}/a.mp4", &s.base[7..]),
            "user name or password",
        ),
    ] {
        let err = download_file(&url, &dest, &policy()).unwrap_err();
        assert!(err.contains(phrase), "{err}");
    }
    assert!(s.requests.lock().unwrap().is_empty());
}

#[test]
fn every_redirect_is_held_to_the_same_rules() {
    let s = serve();
    let dest = tmp("hops");
    for (location, phrase) in [
        ("ftp://127.0.0.1/a.mp4", "only http and https"),
        ("file:///etc/passwd", "only http and https"),
        (
            "http://alice:secret@127.0.0.1/a.mp4",
            "user name or password",
        ),
    ] {
        let url = format!("{}/_/redirect-to?location={location}", s.base);
        let err = download_file(&url, &dest, &policy()).unwrap_err();
        assert!(err.contains(phrase), "{err}");
    }
    let err = download_file(
        &format!("{}/_/redirect/6/media/holiday.mp4", s.base),
        &dest,
        &policy(),
    )
    .unwrap_err();
    assert!(err.contains("too many redirects"), "{err}");
    assert!(left(&dest).is_empty());
}

#[test]
fn a_web_page_and_a_wrong_type_are_refused() {
    let s = serve();
    let dest = tmp("types");
    let err = download_file(&format!("{}/_/html", s.base), &dest, &policy()).unwrap_err();
    assert!(err.contains("web page"), "{err}");
    let video_only = Policy::new(vec!["video/*".into()], 1_000_000);
    let err =
        download_file(&format!("{}/docs/report.pdf", s.base), &dest, &video_only).unwrap_err();
    assert!(err.contains("not a type this skill accepts"), "{err}");
    assert!(left(&dest).is_empty());
}

#[test]
fn a_size_over_the_cap_is_refused_by_header_and_by_stream() {
    let s = serve();
    let dest = tmp("cap");
    let small = Policy::new(vec!["application/octet-stream".into()], 1000);
    for path in ["_/big/5000", "_/big/50000?nolen=1"] {
        let err = download_file(&format!("{}/{path}", s.base), &dest, &small).unwrap_err();
        assert!(err.contains("larger than"), "{path}: {err}");
    }
    assert!(left(&dest).is_empty());
}

#[test]
fn a_silent_server_times_out_and_an_error_status_is_named() {
    let s = serve();
    let dest = tmp("timeout");
    let mut quick = policy();
    quick.read_timeout = Duration::from_millis(500);
    let err = download_file(&format!("{}/_/slow/3", s.base), &dest, &quick).unwrap_err();
    assert!(err.contains("timed out"), "{err}");
    let err = download_file(&format!("{}/nothing/here.mp4", s.base), &dest, &policy()).unwrap_err();
    assert!(err.contains("404"), "{err}");
    assert!(left(&dest).is_empty());
}
