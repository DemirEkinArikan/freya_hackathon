# Direct builder entry point

Both entry points support the same controller options:

```sh
python3.11 builder/build.py --help
python3.11 -m builder.build --help
```

For direct execution, the bootstrap removes the script's `builder` directory
from Python's import search path and adds its parent project directory. This
prevents `builder/platform.py` from shadowing the standard library's `platform`
module during invocation-source recording. Package execution is unchanged.
The bootstrap does not alter brief/API/output confinement or generation inputs.

Real subprocess regressions cover direct help from both project and script
directories, module and isolated help, successful direct/module offline fixture
validation with standard-library runtime provenance, and refusal of an input
outside the checkout. These checks start no generator or platform operation.
Offline validation does not establish guarded generation or hosted behavior;
T05 still requires its complete fresh banking build acceptance.
