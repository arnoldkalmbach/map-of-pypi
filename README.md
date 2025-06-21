# Map of PyPI

This is an interactive map of Python packages published on the Python Package Index (PyPI).  Each dot represents a package.  Two dots are close to each other when the packages share many common downloaders or dependency relationships.

<div align="center">
<img src="public/android-chrome-512x512.png" alt="Map of PyPI logo"/>
</div>
<div align="center">
  <i>Logo by Louise Kashcha, 9 years old. Thank you ❤️</i> 
</div>

## Map releases
- _Initial mock release, May 2025_ – **just 5 packages** to demonstrate the user-interface.

More realistic data sets will appear once the data-engineering pipeline is complete.

## How will it be made?

For this PyPI edition we will analyse two complementary data sources:

1. **Download statistics** – the [pypi.org simple API](https://warehouse.pypa.io/) and publicly available download logs will tell us which packages are installed together most often.
2. **Dependency graph** – every `pyproject.toml` or `setup.py` file already lists a package's direct dependencies.  We will crawl those metadata files and combine the graphs with the download information.

Using these signals we will compute package similarity (again via the [Jaccard Index](https://en.wikipedia.org/wiki/Jaccard_index)), cluster them with [Leiden](https://www.nature.com/articles/s41598-019-41695-z) and layout them with [ngraph.forcelayout](https://github.com/anvaka/ngraph.forcelayout).  Finally all points will be converted into vector tiles and rendered with [maplibre](https://maplibre.org/).

The current repository already contains the full front-end for exploring the map.  The data pipeline is under active development – feel free to follow along or contribute!  For now the `public/mock-data` directory holds a **very tiny** mock dataset so that the site can be run locally and the UI tested.

## Country names

Country naming will follow the same process as the original *Map of GitHub*: creative brainstorming (occasionally assisted by LLMs) and plenty of community feedback.

## Geocoding / search

Search is implemented via client-side fuzzy-matching over a small index – one JSON file per first letter of the package name.  See `public/mock-data/v1/names` for an example.

## License

MIT – see LICENSE file.  If you use the data produced by this project, please consider giving attribution.