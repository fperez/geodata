## Copyright 2016-2017 Gorm Andresen (Aarhus University), Jonas Hoersch (FIAS), Tom Brown (FIAS)

## This program is free software; you can redistribute it and/or
## modify it under the terms of the GNU General Public License as
## published by the Free Software Foundation; either version 3 of the
## License, or (at your option) any later version.

## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.

## You should have received a copy of the GNU General Public License
## along with this program.  If not, see <http://www.gnu.org/licenses/>.


"""
GEODATA

Geospatial Data Collection and "Pre-Analysis" Tools
"""

from __future__ import absolute_import

import xarray as xr
import dask
import pandas as pd
import numpy as np
import os, shutil
import logging
import tempfile
import shutil
import subprocess
import calendar
from glob import glob
from six import itervalues
from six.moves import map
from multiprocessing import Pool

logger = logging.getLogger(__name__)

def cutout_do_task(task, write_to_file=True):
	task = task.copy()
	prepare_func = task.pop('prepare_func')
	if write_to_file:
		datasetfns = task.pop('datasetfns')

	# Force dask to use just one thread (to save memory)
	with dask.config.set(scheduler='single-threaded'):
		try:
			data = prepare_func(**task)
			if data is None:
				data = []

			if write_to_file:
				for yearmonth, ds in data:
					if ds is None:
						continue
					## TODO : rewrite using plain netcdf4 to add variables
					## to the same file one by one
					fn = datasetfns[yearmonth]
					logger.debug("Writing to %s", os.path.basename(fn))
					ds.to_netcdf(fn)
					logger.debug("Write variable(s) %s to %s generated by %s",
								", ".join(ds.data_vars),
								os.path.basename(fn),
								prepare_func.__name__)
			else:
				return data
		except Exception as e:
			logger.exception("Exception occured in the task with prepare_func `%s`: %s",
							prepare_func.__name__, e.args[0])
			raise e

def cutout_prepare(cutout, overwrite=False, nprocesses=None, gebco_height=False):
	"""
	Main preparation function
	"""
	if cutout.prepared and not overwrite:
		logger.info("The cutout is already prepared. If you want to recalculate it, supply an `overwrite=True` argument.")
		return True

	logger.info("Starting preparation of cutout '%s'", cutout.name)

	cutout_dir = cutout.cutout_dir
	yearmonths = cutout.coords['year-month'].to_index()
	xs = cutout.meta.indexes['x']
	ys = cutout.meta.indexes['y']

	# TODO: IF WANT TO APPEND NOT RECREATE CUTOUT
	#	1. (here) uncomment and change yearmonths to only new ones. delete yearmonths line above
	#	2. (elsewhere) uncomment meta_append / append lines

	# if cutout.meta_append == 1:
	# 	# appended meta. prepare only new year-months
	# 	yearmonths = cutout.coords['year-month'].to_index()
	# else:
	# 	yearmonths = cutout.coords['year-month'].to_index()


	if gebco_height:
		logger.info("Interpolating gebco to the dataset grid")
		cutout.meta['height'] = _prepare_gebco_height(xs, ys)

	# Delete cutout_dir
	if os.path.isdir(cutout_dir):
		logger.debug("Deleting cutout_dir '%s'", cutout_dir)
		shutil.rmtree(cutout_dir)

	os.mkdir(cutout_dir)

	# Write meta file
	# TODO
	(cutout.meta_clean
		.unstack('year-month')
		.to_netcdf(cutout.datasetfn()))

	# Compute data and fill files
	tasks = []

	#for series in itervalues(cutout.weather_data_config[cutout.config]):
		# dict of tasks w/structure (tasks_func, prepare_func)
		# .. could be one task and prepare (eg prepare_month_era5)
		# .. or multiple tasks and prepares (eg prepare_influx_ncep)
	series = cutout.weather_data_config[cutout.config]
	series['meta_attrs'] = cutout.meta.attrs
	tasks_func = series['tasks_func']

	# form call to task_func (eg tasks_monthly_ncep)
	# .. **series contains prepare_func
	# returns: dict(prepare_func=prepare_func, xs=xs, ys=ys, year=year, month=month)
	# .. or: dict(prepare_func=prepare_func, xs=xs, ys=ys, fn=next(glob...), engine=engine, yearmonth=ym)
	tasks += tasks_func(xs=xs, ys=ys, yearmonths=yearmonths, **series)
	for i, t in enumerate(tasks):
		def datasetfn_with_id(ym):
			# returns a filename with incrementing id at end eg `201101-01.nc`
			base, ext = os.path.splitext(cutout.datasetfn(ym))
			return base + "-{}".format(i) + ext
		t['datasetfns'] = {ym: datasetfn_with_id(ym) for ym in yearmonths.tolist()}

	logger.info("%d tasks have been collected. Starting running them on %s.",
				len(tasks),
				("%d processes" % nprocesses)
				if nprocesses is not None
				else "all processors")

	pool = Pool(processes=nprocesses)
	try:
		pool.map(cutout_do_task, tasks)
	except Exception as e:
		pool.terminate()
		logger.info("Preparation of cutout '%s' has been interrupted by an exception. "
					"Purging the incomplete cutout_dir.",
					cutout.name)
		shutil.rmtree(cutout_dir)
		raise e
	pool.close()

	logger.info("Merging variables into monthly compound files")

	for fn in map(cutout.datasetfn, yearmonths.tolist()):
		# Find all files with yearmonth prefix eg `201101-XX.nc`
		base, ext = os.path.splitext(fn)
		fns = glob(base + "-*" + ext)
		if len(fns) == 1 and not gebco_height:
			# Just a single file. Simply rename
			os.rename(fns[0], fn)
		else:
			# Multiple files for yearmonth
			#  open_mfdataset: auto-magically determines appropriate concat and merge of datasets
			with xr.open_mfdataset(fns, combine='by_coords') as ds:
				if gebco_height:
					ds['height'] = cutout.meta['height']

			ds.to_netcdf(fn)
			ds.close()		# close xarray access before unlinking (Win32)
			for tfn in fns: os.unlink(tfn)
		logger.debug("Completed files %s", os.path.basename(fn))

	logger.info("Cutout '%s' has been successfully prepared", cutout.name)
	cutout.prepared = True

def cutout_produce_specific_dataseries(cutout, yearmonth, series_name):
	xs = cutout.coords['x']
	ys = cutout.coords['y']
	series = cutout.weather_data_config[series_name].copy()
	series['meta_attrs'] = cutout.meta.attrs
	tasks_func = series['tasks_func']
	tasks = tasks_func(xs=xs, ys=ys, yearmonths=[yearmonth], **series)

	assert len(tasks) == 1
	data = cutout_do_task(tasks[0], write_to_file=False)
	assert len(data) == 1 and data[0][0] == yearmonth
	return data[0][1]

def cutout_get_meta(cutout, xs, ys, years, months=None, **dataset_params):
	# called in cutout.py as `get_meta()`
	#	Loads various metadata (coordinates, dims...) from dataset via dataset_module.prepare_func (eg prepare_meta_merra2)
	#	(Also download files in case of ERA5)

	if months is None:
		months = slice(1, 12)

	ys = _prepare_lat_direction(cutout.dataset_module.lat_direction, ys)

	meta_kwds = cutout.meta_data_config.copy()
	meta_kwds.update(dataset_params)

	# Assign task function here?
	tasks_func = meta_kwds['tasks_func']


	# Get metadata (eg prepare_meta_merra2)
	prepare_func = meta_kwds.pop('prepare_func')
	ds = prepare_func(xs=xs, ys=ys, year=years.stop, month=months.stop, **meta_kwds)
	ds.attrs.update(dataset_params)


	# with metadata, load various parameters
	meta_file_granularity = meta_kwds['file_granularity'];
	month_start = pd.Timestamp("{}-{}".format(years.stop, months.stop))
	ds.coords["year"] = range(years.start, years.stop+1)
	ds.coords["month"] = range(months.start, months.stop+1)

	if meta_file_granularity == 'daily':
		start, second, end = map(pd.Timestamp, ds.coords['time'].values[[0, 1, -1]])
		offset_start = (start - month_start)
		offset_end = (end - (month_start + pd.offsets.MonthBegin()))
		step = (second - start).components.hours
		ds.coords["time"] = pd.date_range(
			start=pd.Timestamp("{}-{}".format(years.start, months.start)) + offset_start,
			end=(month_start + pd.offsets.MonthBegin() + offset_end),
			freq='h' if step == 1 else ('%dh' % step))
	elif meta_file_granularity == 'dailymeans':
		ds.coords["time"] = pd.date_range(
			start=pd.Timestamp("{}-{}-{}".format(years.start, months.start, 1)),
			end=pd.Timestamp("{}-{}-{}".format(years.stop, months.stop, calendar.monthrange(years.stop, months.stop)[1])),
			freq='d')
	elif meta_file_granularity == 'monthly':
		ds.coords["time"] = pd.date_range(
			start=pd.Timestamp("{}-{}".format(years.start, months.start)),
			end=pd.Timestamp("{}-{}".format(years.stop, months.stop)),
			freq='MS')

	ds = ds.stack(**{'year-month': ('year', 'month')})

	# if cutout.meta_append == 1:
	# 	# Append to existing meta
	# 	(ds
	# 		.unstack('year-month')
	# 		.to_netcdf(cutout.datasetfn('meta','2')) )
	#
	# 	with xr.open_mfdataset([cutout.datasetfn(),cutout.datasetfn('meta','2')], combine='by_coords') as ds_comb:
	# 		ds = ds_comb
	# 	ds = ds.stack(**{'year-month': ('year', 'month')})

	return ds

def cutout_get_meta_view(cutout, xs=None, ys=None, years=slice(None), months=slice(None), **dataset_params):
	# called in cutout as `get_meta_view()`
	#	Create subset of metadata based on xs, ys, years, months
	#	Returns None if any of the dimensions of the subset are empty

	meta = cutout.meta
	meta.attrs['view'] = {}

	if xs is not None:
		meta.attrs.setdefault('view', {})['x'] = xs
	if ys is not None:
		meta.attrs.setdefault('view', {})['y'] = _prepare_lat_direction(cutout.dataset_module.lat_direction, ys)

	meta = (meta
			.unstack('year-month')
			.sel(year=years, month=months, **meta.attrs.get('view', {}))
			.stack(**{'year-month': ('year', 'month')}))

	meta = meta.sel(time=slice(*("{:04}-{:02}".format(*ym)
								 for ym in meta['year-month'][[0,-1]].to_index())))

	# Check if returned non-zero subset
	#	Future work: can check if whole subset is available
	dim_len = [len(d) for d in meta.dims]
	logger.info(dim_len)
	if all(d > 0 for d in dim_len):
		return meta
	else:
		return None


def _prepare_gebco_height(xs, ys, gebco_fn=None):
	# gebco bathymetry heights for underwater
	if gebco_fn is None:
		from .config import gebco_path
		gebco_fn = gebco_path

	tmpdir = tempfile.mkdtemp()
	cornersc = np.array(((xs[0], ys[0]), (xs[-1], ys[-1])))
	minc = np.minimum(*cornersc)
	maxc = np.maximum(*cornersc)
	span = (maxc - minc)/(np.asarray((len(xs), len(ys)))-1)
	minx, miny = minc - span/2.
	maxx, maxy = maxc + span/2.

	tmpfn = os.path.join(tmpdir, 'resampled.nc')
	try:
		ret = subprocess.call(['gdalwarp', '-of', 'NETCDF',
							   '-ts', str(len(xs)), str(len(ys)),
							   '-te', str(minx), str(miny), str(maxx), str(maxy),
							   '-r', 'average',
							   gebco_fn, tmpfn])
		assert ret == 0, "gdalwarp was not able to resample gebco"
	except OSError:
		logger.warning("gdalwarp was not found for resampling gebco. "
					   "Next-neighbour interpolation will be used instead!")
		tmpfn = gebco_path

	with xr.open_dataset(tmpfn) as ds_gebco:
		height = (ds_gebco.rename({'lon': 'x', 'lat': 'y', 'Band1': 'height'})
						  .reindex(x=xs, y=ys, method='nearest')
						  .load()['height'])
	shutil.rmtree(tmpdir)
	return height

def _prepare_lat_direction(lat_direction, ys):
	# Check direction of latitudes encoded in dataset, flip if necessary

	if not lat_direction and ys.stop > ys.start:
		logger.warn("ys slices are expected from north to south, i.e. slice(70, 40) for europe.")
		ys = slice(ys.stop, ys.start, -ys.step if ys.step is not None else None)
	if lat_direction and ys.stop < ys.start:
		logger.warn("ys slices are expected from south to north, i.e. slice(40, 70) for europe.")
		ys = slice(ys.stop, ys.start, ys.step if ys.step is not None else None)
	return ys
