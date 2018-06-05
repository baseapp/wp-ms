##WordPress Malware Scanner

## Dependencies

- Python 2.7

- YARA >= 3.0

```
apt-get install yara
```
- Python-YARA

```
pip install yara-python
```


## Usage
```
usage: scanner.py [-h] [-d] [-f] [--days DAYS] [--skip [SKIP [SKIP ...]]] [-v] path

WordPress Malware Scanner

positional arguments:
  path                  Path to scan for WordPress installation. Default :
                        None

optional arguments:
  -h, --help            show this help message and exit
  -d, --deep-scan       Deep scan with YARA rules
  -f, --full-path       Show full path of file.
  --days DAYS           Ignore the file if it is older.
  --skip [SKIP [SKIP ...]]
                        Skip files
  -v, --verbose         Show debug
```
