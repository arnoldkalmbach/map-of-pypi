export async function getPackageInfo(packageName) {
  const url = `https://pypi.org/pypi/${packageName}/json`;
  try {
    const response = await fetch(url);
    if (!response.ok) {
      if (response.status === 404) {
        return { state: 'NOT_FOUND', name: packageName };
      }
      return { state: 'ERROR', error: `HTTP error: ${response.status}` };
    }
    const data = await response.json();
    const info = data.info;
    return {
      state: 'LOADED',
      name: info.name,
      summary: info.summary,
      description: info.description,
      version: info.version,
      license: info.license,
      homepage: info.home_page,
    };
  } catch (err) {
    return { state: 'ERROR', error: err.message };
  }
}

export async function getReadme(packageName) {
  // PyPI JSON API already returns reStructuredText / Markdown in the description field.
  const pkg = await getPackageInfo(packageName);
  if (pkg.state !== 'LOADED') {
    return { state: 'UNAVAILABLE', content: '' };
  }
  return { state: 'LOADED', content: pkg.description };
} 