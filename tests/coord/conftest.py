import os
from pathlib import Path

os.environ.setdefault('MPLCONFIGDIR', str(Path(__file__).resolve().parents[2] /
                                        'cache' / 'matplotlib'))
