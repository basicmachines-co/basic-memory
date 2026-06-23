from types import SimpleNamespace

from basic_memory.runtime import process as process_module
from basic_memory.runtime import runtime_process_rss_bytes


def test_runtime_process_rss_bytes_reads_linux_proc_status(tmp_path) -> None:
    proc_status = tmp_path / "status"
    proc_status.write_text(
        "\n".join(
            [
                "Name:\tpython",
                "VmPeak:\t  999 kB",
                "VmRSS:\t  123 kB",
            ]
        )
    )

    assert runtime_process_rss_bytes(proc_status_path=proc_status) == 123 * 1024


def test_runtime_process_rss_bytes_uses_darwin_rusage_units(
    tmp_path,
    monkeypatch,
) -> None:
    proc_status = tmp_path / "missing-status"
    monkeypatch.setattr(
        process_module,
        "resource",
        SimpleNamespace(
            RUSAGE_SELF=0,
            getrusage=lambda _who: SimpleNamespace(ru_maxrss=2048),
        ),
    )

    assert (
        runtime_process_rss_bytes(
            proc_status_path=proc_status,
            platform_name="darwin",
        )
        == 2048
    )


def test_runtime_process_rss_bytes_converts_non_darwin_rusage_units(
    tmp_path,
    monkeypatch,
) -> None:
    proc_status = tmp_path / "missing-status"
    monkeypatch.setattr(
        process_module,
        "resource",
        SimpleNamespace(
            RUSAGE_SELF=0,
            getrusage=lambda _who: SimpleNamespace(ru_maxrss=2048),
        ),
    )

    assert (
        runtime_process_rss_bytes(
            proc_status_path=proc_status,
            platform_name="linux",
        )
        == 2048 * 1024
    )


def test_runtime_process_rss_bytes_uses_psutil_when_resource_is_unavailable(
    tmp_path,
    monkeypatch,
) -> None:
    proc_status = tmp_path / "missing-status"
    monkeypatch.setattr(process_module, "resource", None)
    assert process_module.psutil is not None
    monkeypatch.setattr(
        process_module.psutil,
        "Process",
        lambda: SimpleNamespace(memory_info=lambda: SimpleNamespace(rss=4096)),
    )

    assert (
        runtime_process_rss_bytes(
            proc_status_path=proc_status,
            platform_name="win32",
        )
        == 4096
    )


def test_runtime_process_rss_bytes_returns_zero_without_rss_source(
    tmp_path,
    monkeypatch,
) -> None:
    proc_status = tmp_path / "missing-status"
    monkeypatch.setattr(process_module, "resource", None)
    monkeypatch.setattr(process_module, "psutil", None)

    assert (
        runtime_process_rss_bytes(
            proc_status_path=proc_status,
            platform_name="win32",
        )
        == 0
    )
