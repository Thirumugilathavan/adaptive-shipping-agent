\# Adaptive Shipping Intelligence Agent



An intelligent shipping decision-support application built for the ZiroDigital Agentic AI Hackathon 2026.



The application helps users compare courier options, optimize packaging, evaluate shipping costs, test how robust a recommendation is, and manage dispatch decisions with operator approval.



\## Features



\- \*\*Courier comparison:\*\* Rank courier options by lowest cost, fastest delivery, or balanced preferences.

\- \*\*Chargeable weight calculation:\*\* Compare actual and volumetric weight using configurable rules.

\- \*\*Cost breakdown:\*\* Display freight, fuel, handling, COD, and tax components using configurable demo assumptions.

\- \*\*Packaging optimization:\*\* Evaluate package fit and compare packaging alternatives.

\- \*\*Stress testing:\*\* Test whether shipping recommendations change when assumptions are adjusted.

\- \*\*Quote freshness:\*\* Check whether quotes have expired or changed before approval.

\- \*\*Controlled dispatch:\*\* Require explicit operator approval and handle uncertain shipment-creation outcomes cautiously.

\- \*\*History and audit:\*\* Store records in SQLite and review previous operations.

\- \*\*Tracking integration:\*\* Includes a client for the documented ShipSandbox tracking endpoint.



\## Technology Stack



\- Python

\- Streamlit

\- Pydantic

\- SQLite

\- Pytest



\## Project Structure



\- `src/domain/` — Shipping calculations and decision logic

\- `src/persistence/` — Database and state management

\- `src/providers/` — Courier provider interfaces and adapters

\- `src/ui/` — Streamlit user interface

\- `tests/` — Automated tests

\- `run\_demo.py` — Command-line demonstration



\## How to Run



\### Prerequisites



\- Python 3.10 or newer

\- pip



\### Install dependencies



Open PowerShell in the project folder and install the dependencies listed in the project's dependency file.



\### Start the application



```powershell

python -m streamlit run src/ui/app.py

```



Open the local URL shown in the terminal. It is normally `http://localhost:8501`.



\### Run automated tests



```powershell

python -m pytest -v

```



\### Run the command-line demo



```powershell

python run\_demo.py

```



\## Important Limitations



\- Demo courier quotes and pricing assumptions are simulated, not live carrier prices.

\- Tracking depends on the documented endpoint being available and correctly configured.

\- Real shipment booking, carrier label generation, and pickup booking must not be assumed unless their provider integrations are verified.

\- Stress-test results measure sensitivity to the scenarios tested; they are not probabilities or guarantees.

\- Dispatch workflows are designed to require operator approval and avoid blind retries when a shipment-creation result is uncertain.



\## Future Improvements



\- Connect verified live carrier rate APIs.

\- Integrate real label generation and shipment booking.

\- Add more carrier-specific serviceability checks.

\- Improve deployment and monitoring.



\## License



No license has been specified yet. Contact the project maintainers before reusing or redistributing this project.



