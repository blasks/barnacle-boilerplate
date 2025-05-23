#!/usr/bin/env python
# AUTHOR: Stephen Blaskowski
# CREATE DATE: 3 February 2025

# Script to determine optimal hyperparameters (number of components and 
# sparsity coefficient) for the sparse tensor decomposition model of 
# Prochlorococcus & Synechococcus residual abundance data, using a parameter
# grid search.

# imports
import argparse
import datetime
import json
import numpy as np
import pandas as pd
import tomli
import xarray as xr

from barnacle import SparseCP
from barnacle.tensors import SparseCPTensor
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from sklearn.model_selection import ParameterGrid
from tensorly import check_random_state
from tensorly.cp_tensor import CPTensor
from tlab.cp_tensor import store_cp_tensor, load_cp_tensor
from tlviz.model_evaluation import relative_sse, core_consistency
from tlviz.factor_tools import factor_match_score, degeneracy_score


def handle_arguments():
    '''
    Returns argument parser.
    '''
    description = '''
        This script performs a cross-validated hyperparameter grid search to
        determine the optimal hyperparameters (rank: number of components, and
        lambda: sparsity coefficient) for fitting a Barnacle SparseCPTensor
        model to data. The script should be called with a config.toml file to 
        define the parameters of the grid search.

        Example usage: ./grid-search.py path/to/config.toml
        
        TOML format:
        
        [grid] # rank and lambda values to be evaluated in grid search (all-by-all)
        ranks = [1, 5, 10, 15, 20]
        lambdas = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]]
        
        [params] # other parameters passed to the SparseCPTensor model, constant across grid search
        nonneg_modes = [1, 2]
        tol = 0.00001
        n_iter_max = 2000
        n_initializations = 5 
        
        [script] # other script parameters including input, output directory, bootstraps, etc.
        input = "data/tensor-data.nc"
        outdir = "data/4-fitting/"
        n_bootstraps = 10
        replicates = ['A', 'B', 'C']
        max_processes = 16
        seed = 9481        
        '''
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('toml', type=str, help='TOML file containing grid search parameters.')
    return parser

# function to return random replicate labelings
def generate_replicate_labels(sample_names, random_state=None, replicate_map=None):
    '''Generates random replicate labels to align with an input vector of sample names.
    
    Parameters
    ----------
    sample_names : np.ndarray
        Input array of sample names. Must be sorted in ascending order.
    random_state : {None, int, numpy.random.RandomState}
        Default is None.
    replicate_map : {None, dict}
        Map of integer labels to preferred replicate labels.
        Example:
            {0:'A', 1:'B', 2:'C'}
        Default is None.
            
    Returns
    -------
    replicate_labels : np.ndarray
        Array of randomly generated replicate lables, to be aligned with input `sample_names`.
    
    '''
    # check that input is a numpy array
    if not type(sample_names) is np.ndarray:
        raise AssertionError('`sample_names` must be a numpy.ndarray')
    # check sample_names are sorted
    if not np.all(sample_names[:-1] <= sample_names[1:]):
        raise AssertionError('`sample_names` must be sorted in ascending order')
    # get counts of each sample name
    names, counts = np.unique(sample_names, return_counts=True)
    # get random state
    rns = check_random_state(random_state)
    # generate replicate labels
    replicate_labels = [rns.choice(np.arange(counts.max()), size=c, replace=False) for c in counts]
    replicate_labels = np.concatenate(replicate_labels)
    # map preferred replicate labels
    if replicate_map is not None:
        mapped_replicate_labels = [replicate_map[i] for i in replicate_labels]
        replicate_labels = np.array(mapped_replicate_labels)
    # return result
    return replicate_labels


# function to separate out subtensors of each replicate
def separate_replicates(dataset, coordinates, data_variable, replicate_label='replicate_id'):
    '''Separates data from each replicate set into its own independent DataArray.
    
    Parameters
    ----------
    dataset : xarray.Dataset
        Dataset with replicates
    coordinates : list of str
        Coordinates to be preserved in each replicate set.
    data_variable : str
        Name of data variable to be selected in each replicate set.
    replicate_label : str, default is 'replicate_id'
        Label of replicate field in `dataset`. 
 
    Returns
    -------
    replicate_sets : dict of xarray.DataArrays
        Set of replicate DataArrays, each keyed on its replicate label.
    
    '''
    # get list of replicate labels
    replicates = np.unique(dataset[replicate_label].to_numpy())
    # pull out each replicate subset
    subsets = list()
    for rep in replicates:
        # pull out only data with of the particular replicate
        df = dataset.where(dataset[replicate_label] == rep, drop=True).to_dataframe().reset_index()
        # reform DataArray with specified coordinates and data_variable
        rep_da = xr.DataArray.from_series(df.set_index(coordinates)[data_variable])
        # add to dict of replicate subsets
        subsets.append(rep_da)
    # arrange in dictionary and return
    return dict(zip(replicates, subsets))


# function to select common indices between two datasets
def select_common_indices(dataset_1, dataset_2, coordinates):
    '''Finds common indices between two datasets.
    
    Parameters
    ----------
    dataset_1 : xarray.Dataset
        Dataset with common coordinates to be compared.
    dataset_2 : xarray.Dataset
        Dataset with common coordinates to be compared.
    coordinates : list of str
        Coordinates to be compared between `dataset_1` and `dataset_2`.
        
    Returns
    -------
    common_index_labels : list of numpy.Arrays
        Common indices' labels, one per coordinate provided.
    indices_1 : list of numpy.Arrays of ints
        Common integer indices, one per coordinate provided.
    indices_2 : list of numpy.Arrays of ints
        Common integer indices, one per coordinate provided.
    '''
    # initialize outputs
    if len(coordinates) > 1:
        common_index_labels = {}
        indices_1 = {}
        indices_2 = {}
    # loop through coordinates
    for coord in coordinates:
        # get shared coordinate labels
        shared_labels = np.intersect1d(
            dataset_1.indexes[coord], 
            dataset_2.indexes[coord], 
            assume_unique=True, 
            return_indices=False
        )
        # get dataset 1 index
        _, index_1, _ = np.intersect1d(
            dataset_1.indexes[coord], 
            shared_labels, 
            assume_unique=True, 
            return_indices=True
        )
        # get dataset 2 index
        _, index_2, _ = np.intersect1d(
            dataset_2.indexes[coord], 
            shared_labels, 
            assume_unique=True, 
            return_indices=True
        )
        # store labels and indices
        if len(coordinates) > 1: 
            common_index_labels[coord] = shared_labels
            indices_1[coord] = index_1
            indices_2[coord] = index_2
    # return results
    if len(coordinates) > 1: 
        return common_index_labels, indices_1, indices_2
    else:
        return shared_labels, index_1, index_2
    
    
# function to select subset of indices in cp tensor
def subset_cp_tensor(cp_tensor, subset_indices):
    '''Selects subset of cp_tensor based on provided indices
    
    Parameters
    ----------
    cp_tensor : tensorly.CPTensor
        CPTensor object with (weights, factors).
    subset_indices : dict(int: index-like)
        Dictionary with mode as key and value an integer index of 
        the positions to be downselected from `cp_tensor`.
        Example: {1: [0, 1, 3, 4, 5, 8]}
        
    Returns
    -------
    subset_cp : tensorly.CPTensor
        Subset CPTensor.
    '''
    weights, factors = cp_tensor
    new_factors = factors.copy()
    for mode, index in subset_indices.items():
        new_factors[mode] = factors[mode][index]
    return(CPTensor((weights, new_factors)))


def fit_save_model(model, data, path, fit_params):
    '''Helper function that takes an instantiated model and data as input,
    fits the model to the data, and returns the fit model. Optionally, the model
    and its settings can be saved to an input file path.
    
    Parameters
    ----------
    model : barnacle.SparseCP
        Instantiated and parameterized SparseCP model.
    data : numpy.ndarray
        Input data tensor.
    path : pathlib.Path
        Path directory where output will be saved. If path=None, no data will be saved.
        If a legitimate filepath is provided, the fit model, in addition to parameters 
        will be saved at the provided filepath.
    fit_params : dict
        Keyword arguments to be passed to the SparseCP.fit_transform() method. 
        Pass empty dictionary if no kwargs are to be passed. 
            
    Returns
    -------
    model : barnacle.SparseCP
        Fit model.
    '''
    if path is not None:
        # make path directories if they don't exist yet
        if not path.exists():
            path.mkdir(parents=True)
        # save parameters
        if model._best_cp_index is not None:
            raise AssertionError('The `model` passed has already been fit')
        else:
            with open(path / 'model-parameters.txt', 'w') as f:
                f.write(json.dumps(model.__dict__, indent=4))
    _ = model.fit_transform(data, return_losses=False, **fit_params)
    # save best fit model
    if path is not None:
        store_cp_tensor(model.decomposition_, path / 'fitted-model.h5')
    # return model
    return model


# function to count number of nonzero components in a cp tensor
def nonzero_components(cp, return_trimmed_cp=False):
    accumulator = np.ones_like(cp.weights)
    for f in cp.factors:
        accumulator *= f.sum(axis=0)
    if return_trimmed_cp:
        raise NotImplementedError
    else:
        return (accumulator != 0.0).sum()


# main experiment script
def main():
    
    # load config toml file
    parser = handle_arguments()
    args = parser.parse_args()
    with open(args.toml, 'rb') as file:
        config = tomli.load(file)
    
    # set random state
    seed = config['script']['seed']
    rns = check_random_state(seed)
    
    # import xarray DataSet (NetCDF4 file)
    dataset = xr.open_dataset(config['script']['input'])
    shuffle_ds = dataset.copy()
    
    # output directory and experiment parameters
    base_dir = Path(config['script']['outdir'])
    n_bootstraps = config['script']['n_bootstraps']
    replicates = config['script']['replicates']
    n_replicates = len(replicates) 
    max_processes = config['script']['max_processes']
    
    # define model grid search param
    model_params = {
        'rank': config['grid']['ranks'], 
        'lambdas': config['grid']['lambdas'], 
        'nonneg_modes': [config['params']['nonneg_modes']],
        'tol': [config['params']['tol']], 
        'n_iter_max': [config['params']['n_iter_max']], 
        'n_initializations': [config['params']['n_initializations']]
    }
    param_grid = list(ParameterGrid(model_params))
    # sort by lambda to make parallelization more efficient
    param_grid = sorted(param_grid, key=lambda d: d['lambdas'][0])
    
    # set up output data records and locations
    filepath_fit_data = base_dir / 'fitting_data.csv'
    if filepath_fit_data.is_file():
        fitting_df = pd.read_csv(filepath_fit_data)
        fitting_results = fitting_df.to_dict('records')
    else:
        fitting_df = pd.DataFrame()
        fitting_results = []
    filepath_cv_data = base_dir / 'cv_data.csv'
    if filepath_cv_data.is_file():
        cv_df = pd.read_csv(filepath_cv_data)
        cv_results = cv_df.to_dict('records')
    else:
        cv_df = pd.DataFrame(
            columns=['bootstrap_id', 'rank', 'lambda', 'modeled_replicate', 
                     'comparison_replicate', 'n_components', 
                     'mode0_factor_sparsity', 'sse', 'fms']
        )
        cv_results = []
    
    # assemble jobs
    models = []
    data_tensors = []
    dirpaths_models = []
    param_kwargs = []
    ledger = {}
    for boot_id in range(n_bootstraps):
        shuffle_seed = rns.randint(2**32)
        print('\nBootstrap: {} (seed={})'.format(boot_id, shuffle_seed), flush=True)
        
        # directory and file paths
        output_dir = base_dir / 'bootstrap{}'.format(boot_id)
        filepath_shuffle_ds = output_dir / 'dataset-bootstrap{}.nc'.format(boot_id)
        
        # import shuffled dataset if it exists
        if filepath_shuffle_ds.is_file():
            print('Importing DataSet discovered at:\n\t{}'.format(
                filepath_shuffle_ds
            ), flush=True)
            shuffle_ds = xr.open_dataset(filepath_shuffle_ds)
        # make and save shuffled dataset if it doesn't exist
        else:
            print('Shuffling DataSet replicate labels', flush=True)
            if not output_dir.is_dir():
                output_dir.mkdir(parents=True)
            # generate new replicate labels
            new_labels = generate_replicate_labels(
                sample_names=shuffle_ds.sample_id.to_numpy(), 
                random_state=shuffle_seed, 
                replicate_map={i: r for i, r in enumerate(config['script']['replicates'])}
            )
            # make Series of new labels with sample as index
            new_labels_series = pd.DataFrame(
                zip(shuffle_ds.sample_replicate_id.to_numpy(), new_labels), 
                columns=['sample_replicate_id', 'replicate_id']
            ).set_index('sample_replicate_id')['replicate_id']
            # assign new replicate labels to copied dataset
            shuffle_ds['replicate_id'] = xr.DataArray.from_series(new_labels_series)
            # save random seed used for shuffling as dataset attribute
            shuffle_ds.attrs['shuffle_seed'] = shuffle_seed
            # save replicate shuffled dataset to netCDF4 file
            shuffle_ds.to_netcdf(filepath_shuffle_ds)
        
        # set up replicate subtensor data
        filepaths_reps = {}
        for rep in replicates:
            path = output_dir / 'replicate{}'.format(rep)
            if not path.is_dir():
                path.mkdir(parents=True)
            # collect all filepaths
            filepaths_reps[rep] = path / 'shuffled-replicate-{}.nc'.format(rep, rep)
        # check if all replicate dataarrays exist or not
        all_reps_saved = np.all([filepaths_reps[f].is_file() for f in replicates])
        # import replicate subtensors if the saved files exist
        if all_reps_saved:
            print('Importing replicate DataArrays discovered at:\n{}'.format(
                json.dumps({i: str(k) for i, k in filepaths_reps.items()}, indent=4)
            ), flush=True)
            replicate_sets = {}
            for rep in replicates:
                replicate_sets[rep] = xr.open_dataarray(filepaths_reps[rep])
        # otherwise separate out replicate sets from shuffled tensor
        else:
            print('Separating shuffled replicate DataArrays', flush=True)
            replicate_sets = separate_replicates(
                shuffle_ds, list(shuffle_ds.coords)[:-1] + ['sample_id'], 'data'
            )
            for rep in replicates:
                # save shuffled replicate data
                replicate_sets[rep].to_netcdf(filepaths_reps[rep])
        
        # fit grid search models to each replicate dataset
        for rep in replicates:
            # pull out shuffled replicate data
            tensor = replicate_sets[rep]
            
            # instantiate models and define output filepaths
            for params in param_grid:
                model_seed = rns.randint(2**32)
                model_dir = output_dir / 'replicate{}/rank{}/lambda{}'.format(
                    rep, params['rank'], params['lambdas'][0]
                )
                filepath_fitted = model_dir / 'fitted-model.h5'
                # don't re-fit any models that have already been fitted
                if not filepath_fitted.is_file():
                    # instantiate parameterized model
                    models.append(SparseCP(**params, random_state=model_seed))
                    data_tensors.append(tensor.data)
                    dirpaths_models.append(model_dir)
                    param_kwargs.append({'threads': 1, 'verbose': 0})
                    ledger[model_seed] = {
                        'bootstrap': boot_id, 
                        'replicate': rep, 
                        'model_dir': model_dir, 
                        'tensor_path': filepaths_reps[rep]
                    }

    # run all assembled jobs in parallel
    print('\nLaunching model fitting in parallel\n', flush=True)
    job_params = (
        models, 
        data_tensors, 
        dirpaths_models, 
        param_kwargs
    )
    executor = ProcessPoolExecutor(max_workers=max_processes)
    fit_models = executor.map(fit_save_model, *job_params)
        
    # iterate through fitted model results
    for model in fit_models:
        
        # calculate metrics
        tensor = xr.open_dataarray(ledger[model.random_state]['tensor_path'])
        rank = model.rank
        lamb = model.lambdas[0]
        best_init = model._best_cp_index
        loss = model.loss_[-1]
        cvg_iter = len(model.loss_)
        sse = relative_sse(model.decomposition_, tensor)
        degeneracy = degeneracy_score(model.decomposition_)
        cc = core_consistency(model.decomposition_, tensor)
        can_fms = [factor_match_score(model.decomposition_, c, consider_weights=False, allow_smaller_rank=True) for c in model.candidates_]
        can_sse = [relative_sse(c, tensor) for c in model.candidates_]
        
        # record metrics
        fitting_results.append(
            {
                'datetime': datetime.datetime.now(), 
                'bootstrap_id': ledger[model.random_state]['bootstrap'], 
                'replicate': ledger[model.random_state]['replicate'], 
                'rank': rank, 
                'lambda': lamb, 
                'best_init': best_init, 
                'loss': loss, 
                'convergence_iterations': cvg_iter, 
                'sse': sse, 
                'degeneracy': degeneracy, 
                'core_consistency': cc, 
                'candidate_fms': can_fms, 
                'candidate_sse': can_sse
            }
        )
        
        # print some metrics
        print('bootstrap: {}, replicate: {}, rank:{}, lambda:{}, sse:{:.5}'.format(
            ledger[model.random_state]['bootstrap'], 
            ledger[model.random_state]['replicate'], 
            rank, 
            lamb, 
            sse, 
        ), flush=True)
    
        # save data
        fitting_df = pd.DataFrame(fitting_results)
        fitting_df.to_csv(filepath_fit_data, index=False)
    
    # shut down executor
    executor.shutdown()

    # collect cross validation results
    print('\nBeginning cross validataion calculations\n', flush=True)
    for boot_id in range(n_bootstraps):
        # set path of bootstrap data
        boot_path = base_dir / 'bootstrap{}'.format(boot_id)
        # read in shuffled replicate data
        rep_data = {}
        for rep in replicates:
            rep_path = boot_path / 'replicate{}'.format(rep) 
            rep_data[rep] = xr.open_dataarray(rep_path / 'shuffled-replicate-{}.nc'.format(rep))
        # iterate through all parameter combos
        for params in param_grid:
            # get all the models
            cps = {}
            expt_path = 'rank{}/lambda{}'.format(params['rank'], params['lambdas'][0])
            for rep in replicates:
                cp_path = boot_path / 'replicate{}'.format(rep) / expt_path
                cps[rep] = load_cp_tensor(cp_path / 'fitted-model.h5')
            for modeled_rep in replicates:
                for comparison_rep in replicates:
                    print_string = 'bootstrap: {}, rank: {}, lambda: {}, modeled: {}, comparison: {}'.format(
                        boot_id, 
                        params['rank'], 
                        params['lambdas'][0], 
                        modeled_rep, 
                        comparison_rep
                    )
                    # check if comparison has already been calculated
                    record = cv_df.loc[(
                        (cv_df['bootstrap_id'] == boot_id) & 
                        (cv_df['rank'] == params['rank']) & 
                        (cv_df['lambda'] == params['lambdas'][0]) & 
                        (cv_df['modeled_replicate'] == modeled_rep) & 
                        (cv_df['comparison_replicate'] == comparison_rep)
                    )]
                    if len(record) >= 1:
                        print(
                            'Pre-existing record found, ' + 
                            'skipping following comparison:\n\t{}'.format(print_string), 
                            flush=True
                        )
                        continue
                    else:
                        print(print_string, flush=True)
                    
                    # find common samples
                    subset_cps = {}
                    if modeled_rep != comparison_rep:
                        common_labels, idx_modeled, idx_comparison = select_common_indices(
                            rep_data[modeled_rep], 
                            rep_data[comparison_rep], 
                            ['sample_id']
                        )
                        # get cp subsets
                        subset_cps[modeled_rep] = subset_cp_tensor(
                            cps[modeled_rep], 
                            {2: idx_modeled}
                        )
                        subset_cps[comparison_rep] = subset_cp_tensor(
                            cps[comparison_rep], 
                            {2: idx_comparison}
                        )
                        # get comparison data
                        comparison_data = rep_data[comparison_rep].sel(
                            sample_id=common_labels
                        )                        
                    else:
                        # cp subset is full model
                        subset_cps[modeled_rep] = cps[modeled_rep]
                        # comparison data is full replicate set
                        comparison_data = rep_data[comparison_rep]
                    
                    # calculate fms & cosine similiary scores against other fit models
                    if modeled_rep < comparison_rep:
                        fms_cv = factor_match_score(
                            subset_cps[modeled_rep], 
                            subset_cps[comparison_rep], 
                            consider_weights=False, 
                            allow_smaller_rank=True
                        )
                    else:
                        # skip redundant and self comparisons
                        fms_cv = np.nan
                    # calculate relative sse
                    rel_sse = relative_sse(subset_cps[modeled_rep], comparison_data.data)
                    # keep results
                    cv_results.append(
                        {
                            'bootstrap_id': boot_id, 
                            'rank': params['rank'], 
                            'lambda': params['lambdas'][0], 
                            'modeled_replicate': modeled_rep, 
                            'comparison_replicate': comparison_rep, 
                            'n_components': nonzero_components(cps[modeled_rep]), 
                            'mode0_factor_sparsity': 
                                1 - (cps[modeled_rep].factors[0] != 0.0).mean(axis=0).mean(), 
                            'sse': rel_sse, 
                            'fms': fms_cv, 
                        }
                    )
        # store results in dataframe and save
        cv_df = pd.DataFrame(cv_results)
        cv_df.to_csv(filepath_cv_data, index=False)

if __name__ == "__main__":
  main()
