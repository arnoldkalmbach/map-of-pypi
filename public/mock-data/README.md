# Mock Data for Local Development

This directory mirrors the structure of the real data buckets that the production **Map of PyPI** site will consume.  It is intentionally tiny so the application can boot without requiring gigabytes of vector-tiles.

```
public/mock-data/
  v1/
    borders.geojson        – polygon "countries" (only one rectangle for now)
    places.geojson         – labels for countries (empty)

    names/
      c.json               – fuzzy-search index for packages starting with "c"
      i.json               – … "i"
      r.json               – … "r"
      u.json               – … "u"

    graphs/
      0.graph.dot          – directed graph connecting five sample packages
```

### File Formats & Purpose

* **borders.geojson** – GeoJSON *FeatureCollection* of polygons.  Each feature **must** have:
  * `id`            – integer country id (used as *groupId* elsewhere)
  * `properties.fill` – a colour that matches one row of `src/lib/getColorTheme.js` so the country is rendered in the desired palette.

* **places.geojson** – Optional *Point* features used as country labels.  Empty in this mock.

* **names/*.json** – One flat JSON array per first letter.  Each row is:
  ```json
  ["package-name", <lon>, <lat>]
  ```
  This is loaded by the fuzzy-searcher so the autocomplete can work offline.

* **graphs/*.graph.dot** – GraphViz DOT files, one per country id (`<groupId>.graph.dot`).  Node syntax:
  ```dot
  "packageName" [l="<lon>,<lat>"];
  ```
  Links are plain `A -> B;`.  If you need to mark an *external* edge (to a node in another group) add `e="1"` to the link attributes.

### Adding More Mock Packages
1. Choose a country id (e.g. `0`).  All packages you add to `graphs/0.graph.dot` belong to that id unless their links have `e="1"`.
2. Append package nodes with coordinates.
3. Add the same package names to the correct `names/<first-letter>.json` list.

That's all—no tiles are needed for local testing.

### Automatic generation of a larger mock set (100 scientific packages)

If you need a slightly more realistic dataset, run the helper script from the project root:

```bash
python scripts/generate_mock_data.py
```

This will parse `raw_data/100_sci_packages.json` and regenerate the following assets inside `v1/`:

* `graphs/0.graph.dot` – 100 nodes laid out in a 10×10 grid, with edges derived from declared `requires_dist` dependencies (only if the target package is part of the 100-package subset).
* `names/*.json` – one fuzzy-search list per first letter, updated to include every new package.
* `places.geojson` – a single *Point* label marking the centroid of the new package cloud.

The original ultra-tiny sample will be overwritten, so if you need the initial five-package map, make a copy first. 