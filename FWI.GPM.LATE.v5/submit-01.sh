#!/usr/bin/env bash
#SBATCH --account=s3673
#SBATCH --time=11:59:00
#SBATCH --cpus-per-task=40
#SBATCH --mem=128G
#SBATCH --constraint='sky|cas|hasw'
#SBATCH --output=logs/create-zarr-%j.log

source ~/.bash_functions
mod_py39

conda run -n vdp-common python -u 01-create-zarr.py > logs/01-create-zarr.py
RESULT=$?
if [[ $RESULT -eq 0 ]]; then
  echo "Done!"
else
  echo "Error executing Python script"
  exit $RESULT
fi
