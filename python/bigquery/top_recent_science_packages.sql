WITH science_packages AS (
  SELECT
    name,
    ANY_VALUE(description) as description,
    ANY_VALUE(classifiers) as classifiers,
    ANY_VALUE(requires_dist) as requires_dist
  FROM `bigquery-public-data.pypi.distribution_metadata`
  WHERE 
    'Intended Audience :: Science/Research' in UNNEST(classifiers)
    GROUP BY name
),
download_counts AS (
  SELECT
    project,
    COUNT(*) as num_downloads
  FROM `bigquery-public-data.pypi.file_downloads`
  WHERE
    timestamp > TIMESTAMP("2025-06-01 00:00:00")
  GROUP BY project
)
SELECT *
FROM science_packages
LEFT JOIN download_counts
ON science_packages.name = download_counts.project
ORDER BY num_downloads DESC
LIMIT 10000