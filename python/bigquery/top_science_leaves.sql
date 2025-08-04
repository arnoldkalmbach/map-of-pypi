-- This query identifies packages that are "sinks" or "leaf nodes" in the
-- dependency graph, meaning they are not listed as a requirement by any other
-- package distribution in the PyPI dataset.

WITH
  -- This CTE selects the most recent version of each package.
  science_packages AS (
    SELECT
      name,
      version,
      description,
      classifiers,
      requires_dist,
      LOWER(REPLACE(name, '_', '-')) AS normalized_name
    FROM (
      SELECT
        name,
        version,
        description,
        classifiers,
        requires_dist,
        -- We are using ROW_NUMBER to pick the most recent version of each package.
        ROW_NUMBER() OVER (
          PARTITION BY name
          ORDER BY version DESC
        ) AS rn
      FROM `bigquery-public-data.pypi.distribution_metadata`
      WHERE 'Intended Audience :: Science/Research' in UNNEST(classifiers)
    )
    WHERE rn = 1
  ),

  -- Get all unique, normalized dependency names from requires-dist
  all_dependencies AS (
    SELECT DISTINCT
      -- Extract the package name before any special characters.
      -- Then, normalize it in the same way as the package names.
      LOWER(REPLACE(REGEXP_EXTRACT(requirement, r'^[a-zA-Z0-9._-]+'), '_', '-')) AS normalized_dependency_name
    FROM
      `bigquery-public-data.pypi.distribution_metadata`,
      UNNEST(requires_dist) AS requirement
    WHERE
      requirement IS NOT NULL
  ),

  -- Get download counts for each package
  download_counts AS (
    SELECT
      project,
      COUNT(*) as num_downloads
    FROM `bigquery-public-data.pypi.file_downloads`
    -- If we don't sample, this query is very expensive
    -- Can trade off sample size for window size
    -- TODO: If table is partitioned on timestamp, tablesample might be too biased
    TABLESAMPLE SYSTEM (2 PERCENT)
    WHERE timestamp > TIMESTAMP("2025-01-01 00:00:00")
    GROUP BY project
  )

-- Find packages that are in all_packages but NOT in all_dependencies.
SELECT
  science_packages.name,
  science_packages.description,
  science_packages.requires_dist,
  download_counts.num_downloads
FROM science_packages
LEFT JOIN all_dependencies
  ON science_packages.normalized_name = all_dependencies.normalized_dependency_name
JOIN download_counts
ON science_packages.name = download_counts.project
WHERE all_dependencies.normalized_dependency_name IS NULL
ORDER BY download_counts.num_downloads DESC