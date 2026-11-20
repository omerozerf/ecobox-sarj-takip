// The two areas of the display scope (data.json `areas`): the primary one ("Ego, F Blok, DC")
// shown as the page always showed it, the secondary one ("Arge tarafı") present everywhere but
// quieter. A renderer written for one set of figures gets an area as `{...data, ...area}`, so
// it reads `heatmap`, `ranking`, `coverage` … of that area and everything else from data.json.

/** {primary, secondary, view(area), socketsOf(area)}; secondary is null without a split. */
export function areaModel(data) {
  const areas = data.areas;
  const primary = areas.find((a) => a.key === 'primary');
  const secondary = areas.find((a) => a.key === 'secondary' && a.sockets.length) ?? null;
  return {
    primary,
    secondary,
    view: (area) => ({ ...data, ...area }),
    socketsOf: (area) => data.sockets.filter((s) => s.area === area.key),
  };
}
