Downlink Simulation

Run these commands from the project root with Python 3.10 or newer. The supplied TLE archive and daily weather data cover the 2025 simulation.

Install the required Python packages first.
python -m pip install -r requirements.txt

Run the annual simulation, then generate the two figures from its saved results.
python src/simulate.py
python figures/plot.py

Daily records, the daily table and summary statistics are saved in results/. The figures are saved in figures/ as daily_downlink_capacity.pdf and .png, and monthly_link_conditions.pdf and .png. The first shows daily effective capacity and its seven-day mean; the second shows monthly SNR, rain loss and free-space loss. Existing files with the same names are overwritten.

To inspect one UTC day, use the following command. It prints the record without saving files.
python src/simulate.py --date 20250101

Tests are optional checks of the link calculations, known results, missing-data handling and download validation. They do not generate the final simulation results or figures.
python -m unittest discover -s tests -v

Downloading is optional and can be skipped when using the supplied archive. To refresh it, set the SPACETRACK_USER and SPACETRACK_PASS environment variables to your Space-Track credentials, then run:
python src/download_tle.py

Without credentials, the downloader reports a setup message and exits without changing the archive.

For available options, run:
python src/simulate.py --help
python figures/plot.py --help
python src/download_tle.py --help
