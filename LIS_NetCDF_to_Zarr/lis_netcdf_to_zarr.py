"""
This script is a Pangeo Forge Recipe for converting NetCDFs generated by
NASA's Land Information System into chunked Zarr stores with consolidated metadata.
The script reads a YAML file to set arguments (see configs/ for examples). This script
assumes that the user is running this on AWS. Inputs and outputs are assumed to be hosted
in an accessible S3 bucket.

Author: Brendan McAndrew SSAI/GSFC (Code 617) - 08/30/2021 (updated: 05/10/2022)
"""

import argparse, logging, s3fs, tempfile, yaml

# provides CLI progress bar
from tqdm import tqdm

# local filesystem utility
from fsspec.implementations.local import LocalFileSystem

# pangeo forge recipe classes and functions
from pangeo_forge_recipes.patterns import pattern_from_file_sequence
from pangeo_forge_recipes.recipes import XarrayZarrRecipe
from pangeo_forge_recipes.storage import FSSpecTarget, CacheFSSpecTarget, MetadataTarget, StorageConfig

# numpy and xarray needed for preprocessing step
import numpy as np
import xarray as xr

if __name__ == '__main__':

    # establish interface to S3 filesystem
    s3 = s3fs.S3FileSystem(anon=False)

    # define protocol scheme
    protocol = 's3://'

    #### parse CLI arguments ####

    # create argument parser
    parser = argparse.ArgumentParser(description="Convert LIS output from NetCDF to Zarr on AWS.")

    # add config file argument
    parser.add_argument('config', metavar='CFG', type=str,
                       help='Path to a YAML configuration file')

    # parse args
    args = parser.parse_args()

    # TODO: add validation for config file

    # parse YAML config file
    with open(args.config) as f:
        config_dict = yaml.safe_load(f)

    # convert YAML key,value pairs to local variables
    globals().update(config_dict)

    # turn on pangeo_forge_recipes logging?
    if enable_logging:
        logger = logging.getLogger('pangeo_forge_recipes')
        formatter = logging.Formatter('%(name)s:%(levelname)s - %(message)s')
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)
        handler.setFormatter(formatter)
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)


    def build_url(path):
        """
        Create an S3 URL given a path. Uses bucket name from config file.
        """
        return protocol + '/'.join([bucket, path])

    # create URL to bucket containing input files
    input_url = build_url(input_path)
    
    # create input URLs by globbing input URL pattern
    input_urls = [protocol + s for s in s3.glob(input_url)]

    # define recipe file pattern
    pattern = pattern_from_file_sequence(input_urls,                      # source paths
                                         'time',                          # concat dimension
                                         nitems_per_file=nitems_per_file) # items per file


    # define LIS output specific preprocessing func
    def add_latlon_coords(ds: xr.Dataset)->xr.Dataset:
        """
        Adds lat/lon as dimensions and coordinates to an xarray.Dataset object.

        LIS NetCDF output contains lat/lon as variables (2D arrays) and logical indices
        for the grid in coordinates named 'east_west' and 'north_south'.

        It is not possible to assign the lat/lon values as coordinates directly because
        LIS outputs may contain masked areas where the lat/lon values are
        set to a nodata value and xarray does not allow use of a coordinates
        that contain NaN values. Instead, we build the coordinate fields based
        on the grid description in the NetCDF metadata. It ain't pretty, but it works.
        """

        # get attributes from dataset
        attrs = ds.attrs

        # get x and y resolution from metadata
        dx = round(float(attrs['DX']), 3)
        dy = round(float(attrs['DY']), 3)

        # get number of grid cells in x, y dimensions from metadata
        ew_len = len(ds['east_west'])
        ns_len = len(ds['north_south'])

        # get lower-left lat and lon from metadata
        ll_lat = round(float(attrs['SOUTH_WEST_CORNER_LAT']), 3)
        ll_lon = round(float(attrs['SOUTH_WEST_CORNER_LON']), 3)

        # calculate upper-right lat and lon
        ur_lat =  ll_lat + (dy * ns_len)
        ur_lon = ll_lon + (dx * ew_len)

        # define the new coordinates
        coords = {
            # create arrays containing the lat/lon at each gridcell
            'lat': np.linspace(ll_lat, ur_lat, ns_len, dtype=np.float32, endpoint=False),
            'lon': np.linspace(ll_lon, ur_lon, ew_len, dtype=np.float32, endpoint=False)
        }

        # rename the original lat and lon variables to preserve them
        ds = ds.rename({'lon':'orig_lon', 'lat':'orig_lat'})

        # rename the grid dimensions to lat and lon
        ds = ds.rename({'north_south': 'lat', 'east_west': 'lon'})

        # assign the coords above as coordinates and add original metadata
        ds = ds.assign_coords(coords)
        ds.lon.attrs = ds.orig_lon.attrs
        ds.lat.attrs = ds.orig_lat.attrs

        return ds


    def preprocess(ds):
        """
        Perform preprocessing of dataset before conversion.
        """
        # add lat/lon coordinates and dimensions
        ds = add_latlon_coords(ds)
        
        return ds
    

    ##### Create storage configuration for target and caches #####
    
    def create_storage_config():
    
        # define local FS object
        fs_local = LocalFileSystem()

        # create cache FS object (uses temp_dir passed in from config)
        fs_temp = CacheFSSpecTarget(fs_local, temp_dir)

        # create target FS object
        target_url = build_url(target_path)
        fs_target = FSSpecTarget(fs=s3, root_path=target_url)

        # create metadata target path and FS object
        meta_dir = tempfile.TemporaryDirectory(dir=temp_dir)
        fs_meta = MetadataTarget(fs_local, meta_dir.name)

        # create storage configuration for target and caches
        return StorageConfig(fs_target, fs_temp, fs_meta)

    ##### Create the recipe #####
    
    print('Creating recipe...')

    recipe = XarrayZarrRecipe(pattern,                           # file URL pattern
                              inputs_per_chunk=inputs_per_chunk, # input files per chunk
                              storage_config=create_storage_config(),     # storage configuration for target and caches
                              process_chunk=preprocess,          # preprocess func
                              cache_inputs=False,                # read inputs directly from S3
                              target_chunks=target_chunks)       # set chunking scheme for output


    # get list of all chunks
    all_chunks = list(recipe.iter_chunks())

    # prepare the target (create empty Zarr store)
    print('Preparing target...')
    recipe.prepare_target()

    # execute the recipe (conversion)
    print('Executing recipe...')
    for chunk in tqdm(recipe.iter_chunks(), total=len(all_chunks)):
        recipe.store_chunk(chunk)

    # consolidate metadata
    print('Finalizing recipe...')
    recipe.finalize_target()

    print('Recipe executed!')
