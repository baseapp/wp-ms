# wp-ms
WordPress Malware Scanner

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


## Run
`python scanner.py </path/to/wordpress/> -s -d`

`-s` | Send file hash to database

`-d` | Run deep scan with YARA rules

