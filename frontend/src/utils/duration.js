export function formatDurationHoursMinutes(durationSeconds) {
  if (durationSeconds === null || durationSeconds === undefined || durationSeconds === "") {
    return "—";
  }

  const seconds = Number(durationSeconds);

  if (!Number.isFinite(seconds) || seconds < 0) {
    return "—";
  }

  const totalMinutes = Math.round(seconds / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;

  if (hours === 0) {
    return `${minutes} min`;
  }

  if (minutes === 0) {
    return `${hours} hr`;
  }

  return `${hours} hr ${minutes} min`;
}
