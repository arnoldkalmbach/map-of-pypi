const hostName = window.location.hostname;
// In dev we serve everything from /mock-data/ folder. In production the data will be hosted as static assets
// under map-of-pypi-data GitHub Pages site (adjust the URL when you decide on final hosting).
const isDev = hostName !== 'anvaka.github.io';
const server = isDev ? `${window.location.origin}/mock-data/` : 'https://your-username.github.io/map-of-pypi-data/';
const params = new URLSearchParams(window.location.search);
const version = params.get('v') || 'v1';

export default {
  serverUrl: '',
  // vectorTilesSource: 'http://192.168.86.79:8082/data/cities.json',
  vectorTilesTiles: `${server}${version}/points/{z}/{x}/{y}.pbf`,
  glyphsSource: `${server}/fonts/{fontstack}/{range}.pbf`,
  bordersSource: `${server}${version}/borders.geojson`,
  placesSource: `${server}${version}/places.geojson`,

  namesEndpoint: `${server}${version}/names`,
  graphsEndpoint: `${server}${version}/graphs`,
};