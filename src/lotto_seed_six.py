"""Search >=3 odd + >=3 even training hits with the unchanged 128-bit generator."""
from lotto_seed_five import main as run_search


def main(argv=None):
    return run_search(argv, minimum_even=3)


if __name__=="__main__":
    raise SystemExit(main())
