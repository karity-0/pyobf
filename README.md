# pyobf

Python obfuscator

Use **Obfuscate Script** for the editor, or **Obfuscate Project** for a folder. Projects are saved to a new `.obf` folder with resources included.

```sh
pip install -r requirements.txt
python main.py
```

```python
@protect_start(cff, bcf, junk, proxy, morph, integrity)
message = @{"hello world"}
print(message)
@protect_end
```

Keep the `.py` source when using `integrity`.
