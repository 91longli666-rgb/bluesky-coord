"""Plot local demo outputs; no upstream archive/provenance input is needed."""
import json
from pathlib import Path
from package_results import plots, trajectory_plot

if __name__ == '__main__':
    root = Path(__file__).resolve().parent / 'results'
    data = json.loads((root / 'pilot/results.json').read_text())
    plots(data, root / 'demo-comparison.png')
    trajectory_plot(root / 'pilot', case='radial4', seed=1, output=root / 'demo-trajectories.png')
    print('Demo chart:', root / 'demo-comparison.png')
