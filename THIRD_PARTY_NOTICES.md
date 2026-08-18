# Third-Party Notices

This file records the direct runtime and build dependencies used by
`stock_analysis` v1.3.0. The upstream license and notice files govern each
dependency. The Windows installer may also contain transitive dependencies
pulled in by these packages; their upstream metadata remains authoritative.

## Direct runtime dependencies

| Package | License | Project / license reference |
|---|---|---|
| pandas | BSD 3-Clause | <https://pandas.pydata.org/> |
| NumPy | BSD 3-Clause | <https://numpy.org/> |
| Tushare | BSD 3-Clause | <https://tushare.pro/> |
| AkShare | MIT | <https://github.com/akfamily/akshare> |
| DuckDB | MIT | <https://duckdb.org/> |
| Apache Arrow / PyArrow | Apache-2.0 | <https://arrow.apache.org/> |
| OpenAI Python client | Apache-2.0 | <https://github.com/openai/openai-python> |
| Pydantic | MIT | <https://github.com/pydantic/pydantic> |
| pydantic-settings | MIT | <https://github.com/pydantic/pydantic-settings> |
| PyYAML | MIT | <https://pyyaml.org/> |
| Jinja2 | BSD 3-Clause | <https://jinja.palletsprojects.com/> |
| Typer | MIT | <https://github.com/fastapi/typer> |
| Rich | MIT | <https://github.com/Textualize/rich> |
| Loguru | MIT | <https://github.com/Delgan/loguru> |
| python-dotenv | BSD 3-Clause | <https://github.com/theskumar/python-dotenv> |
| Plotly | MIT | <https://github.com/plotly/plotly.py> |

## Build and packaging tools

- PyInstaller is used only for building the Windows application. It is
  distributed under GPLv2-or-later with the PyInstaller exception for built
  applications: <https://pyinstaller.org/en/stable/license.html>.
- Inno Setup is used only to create the installer. Its license is available at
  <https://jrsoftware.org/isinfo.php>.

## Project license

The project code is distributed under the MIT License in `LICENSE`. This
notice does not grant permission to use any third-party name or trademark.
