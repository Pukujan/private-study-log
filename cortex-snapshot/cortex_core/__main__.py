from .audit import main as audit_main
from .audit import read_main as audit_read_main
from .doctor import main as doctor_main
from .fetch import main as fetch_main
from .search import main as search_main
from .update import main as update_main


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        import sys

        argv = sys.argv[1:]
    if not argv:
        return search_main(argv)
    command = argv[0]
    rest = argv[1:]
    if command in {"--index", "--hybrid", "--status"}:
        return search_main(argv)
    if command == "search":
        return search_main(rest)
    if command == "write-log":
        return audit_main(rest)
    if command == "audit":
        return audit_read_main(rest)
    if command == "fetch-doc":
        return fetch_main(rest)
    if command == "doctor":
        return doctor_main(rest)
    if command == "update":
        return update_main(rest)
    return search_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
