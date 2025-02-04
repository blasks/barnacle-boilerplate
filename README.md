# barnacle-boilerplate
Quickstart notebooks and scripts for using Barnacle to analyze your data

## Step 1: Set up your environment
1. Copy the contents of this repository to a working directory on your system. This could be accomplished by cloning the repository locally, or downloading all the files to a local directory.
1. Install [Barnacle](https://github.com/blasks/barnacle) and other project tools
    1. Install [Poetry](https://python-poetry.org/) on your system. Poetry is a dependency management tool for Python packages. You can find [installation instructions](https://python-poetry.org/docs/#installation) on the Poetry website.
        - Make sure you install Poetry as directed on the website (do not use `pip`; you can use `pipx` instead).
        - If Poetry is already installed on your system, make sure it is up to date by running `pipx upgrade poetry`. 
    1. Navigate to your working directory and run `poetry install` to install your virtual environment based on the formula in the [`pyproject.toml`](https://github.com/blasks/barnacle-boilerplate/blob/main/pyproject.toml) file.

    #### Or use a container

    1. If it's not already installed, install [docker](https://www.docker.com/get-started/). 
    
    1. Navigate to the repository directory and run
    
        ```docker build -t barnacle .```

    1. Start a bash shell in the cointainer using `docker run barnacle`
    
    1. If you need to make the container portable (e.g. for use on a remote server that uses [singularity/apptainer](https://apptainer.org/docs/user/main/introduction.html)) 
    
        - You can create a [dockerhub](https://app.docker.com/) account and then [push the image](https://docs.docker.com/docker-hub/quickstart/) with tag: `<dockerhub account>/barnacle`

            Then you can build the singularity image using: 

            ```singularity build barnacle.sif docker://<dockerhub account>/barnacle```

        - Alternatively you can save the container to a tarball

            ```docker save barnacle | gzip > barnacle.tar.gz```

            and then transfer the file. You can build the the apptainer image using

            ```apptainer build barnacle.sif docker-archive://barnacle.tar.gz``` 
    
    1. Start a bash shell in the container using `apptainer run barnacle.sif`



## Step 2: Assemble your data
1. Create a csv of your data in [tidy format](https://tidyr.tidyverse.org/articles/tidy-data.html)
    1. Each dimension of your tensor should have it's own column, in addition to a column designating sample replicate and another for your data itself. For example, your metatranscriptomic data might have these column names: `'gene', 'taxon', 'sample', 'replicate', 'transcript_count'`
1. Normalize your data. This step will affect the interpretation of the Barnacle components that result from your analysis. The choice of normalization method is highly dependent on A) your data type and B) the types of patterns you're interested in trying to find in your data (e.g. in metatranscriptomic data, changes in organism abundance vs. changes in transcriptional regulation).
    1. In [our analysis of marine metatranscriptomes](https://doi.org/10.1101/2024.07.15.603627), we used the [sctransform](https://satijalab.org/seurat/articles/sctransform_vignette) package to normalize transcript counts, because this allowed us to remove the effect of variables we weren't interested in analyzing, like differences in organism abundance and sequencing depth. Sctransform also provides an elegant way of handling zero-values and overdispersion in the transcript abundance distributions (variance increases with increasing mean abundance) -- properties [typical of metatranscriptomic data](https://doi.org/10.1186/s13059-017-1359-z).
    1. If you would like to use sctransform to normalize your data, you can run the [`2-normalize-sctransform.ipynb`](https://github.com/blasks/barnacle-boilerplate/blob/main/2-normalize-sctransform.ipynb) notebook. Note that you will first need to [install R](https://cran.r-project.org/) and [install sctransform](https://github.com/satijalab/sctransform) on your system in order to run this notebook.

## Step 3: Build your data tensor
1. Run the [`3-tensorize-data.ipynb`](https://github.com/blasks/barnacle-boilerplate/blob/main/3-tensorize-data.ipynb) notebook to arrange your normalized data into tensor format, using the Python package [xarray](https://docs.xarray.dev/en/stable/).

## Step 4: Fit Barnacle to your data
1. Run the `4-fit-barnacle.ipynb` notebook to find the best parameters to fit Barnacle to your data.
    1. This step can take a long time to run depending on the size and shape of your data tensor and the number of parameters you test.
    1. You may have to iterate over this step several times to hone in on the best parameters.
1. Fit Barnacle to your data using the best parameters you identified.
    1. If you have replicates, you can run bootstraps to help assign confidence values to the associations modeled by components in your analysis.
1. Arrange and save your final model

## Step 5: Analyze the results
1. Run the `5-analyze-results.ipynb` notebook to explore your final model.
    1. The most useful analysis and visualization of your results depends on the type of data you're analyzing, as well as the size and shape of your data tensor. Because of this, the examples may have to be modified for your use case. This notebook is designed to give you a few ideas of where to start your exploration, and inspire your creativity about where to go from here with your analysis!
