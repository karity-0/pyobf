# pyobf

Python obfuscator

```sh
pip install -r requirements.txt
python main.py
```

```python
@protect_start(cff, junk)
message = @{"hello world"}
print(message)
@protect_end
```
