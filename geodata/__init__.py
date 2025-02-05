## Copyright 2020 Michael Davidson (UCSD), William Honaker.

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

from .cutout import Cutout
from .mask import Mask
from .dataset import Dataset
from .plot import *
from ._version import __version__

__author__ = "Michael Davidson (UCSD), William Honaker"
__copyright__ = "GNU GPL 3 license"
