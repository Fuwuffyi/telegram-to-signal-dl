{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
   buildInputs = [
      pkgs.python3
      pkgs.python3Packages.venvShellHook
   ];

   venvDir = "./.venv";

   postVenvCreation = ''
      pip install -r requirements.txt
   '';
}

