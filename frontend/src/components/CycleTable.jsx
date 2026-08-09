export default function CycleTable({ cycles, selectedCycles, setSelectedCycles }) {
  const toggleCycle = (idx) => {
    if (selectedCycles.includes(idx)) {
      setSelectedCycles(selectedCycles.filter((x) => x !== idx));
    } else {
      setSelectedCycles([...selectedCycles, idx]);
    }
  };

  return (
    <div>
      <h2>Detected Cycles</h2>

      <div className="table-wrapper">
        <table className="responsive-table">
          <thead>
            <tr>
              <th>Use</th>
              <th>Cycle No</th>
              <th>Start</th>
              <th>End</th>
              <th>Duration (s)</th>
              <th>Max</th>
              <th>Min</th>
            </tr>
          </thead>
          <tbody>
            {cycles.map((cycle, idx) => (
              <tr key={idx}>
                <td>
                  <input
                    type="checkbox"
                    checked={selectedCycles.includes(idx)}
                    onChange={() => toggleCycle(idx)}
                  />
                </td>
                <td>{cycle.cycle_no}</td>
                <td className="cell-wrap">{cycle.start}</td>
                <td className="cell-wrap">{cycle.end}</td>
                <td>{cycle.duration_seconds.toFixed(1)}</td>
                <td>{cycle.max_value.toFixed(3)}</td>
                <td>{cycle.min_value.toFixed(3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}