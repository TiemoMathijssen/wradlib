#-------------------------------------------------------------------------------
# Name:         clutter
# Purpose:
#
# Authors:      Maik Heistermann, Stephan Jacobi and Thomas Pfaff
#
# Created:      26.10.2011
# Copyright:    (c) Maik Heistermann, Stephan Jacobi and Thomas Pfaff 2011
# Licence:      The MIT License
#-------------------------------------------------------------------------------
#!/usr/bin/env python

"""
Raw Data I/O
^^^^^^^^^^^^

Please have a look at the tutorial :doc:`tutorial_supported_formats` for an introduction
on how to deal with different file formats.

.. autosummary::
   :nosignatures:
   :toctree: generated/

   readDX
   writePolygon2Text
   read_EDGE_netcdf
   read_BUFR
   read_OPERA_hdf5
   read_GAMIC_hdf5
   read_RADOLAN_composite

"""

# standard libraries

import sys
import re
import datetime as dt
import pytz
import cPickle as pickle
import os

# site packages
import h5py
import numpy as np
import netCDF4 as nc # ATTENTION: Needs to be imported AFTER h5py, otherwise ungraceful crash

# wradib modules
import wradlib.bufr as bufr


# current DWD file naming pattern (2008) for example:
# raa00-dx_10488-200608050000-drs---bin
dwdpattern = re.compile('raa..-(..)[_-]([0-9]{5})-([0-9]*)-(.*?)---bin')


def _getTimestampFromFilename(filename):
    """Helper function doing the actual work of getDXTimestamp"""
    time = dwdpattern.search(filename).group(3)
    if len(time) == 10:
        time = '20' + time
    return dt.datetime.strptime(time, '%Y%m%d%H%M')


def getDXTimestamp(name, tz=pytz.utc):
    """Converts a dx-timestamp (as part of a dx-product filename) to a python datetime.object.

    Parameters
    ----------
    name : string representing a DWD product name

    tz : timezone object (see pytz package or datetime module for explanation)
         in case the timezone of the data is not UTC

    opt : currently unused

    Returns
    -------
    time : timezone-aware datetime.datetime object
    """
    return _getTimestampFromFilename(name).replace(tzinfo=tz)


def unpackDX(raw):
    """function removes DWD-DX-product bit-13 zero packing"""
    # data is encoded in the first 12 bits
    data = 4095
    # the zero compression flag is bit 13
    flag = 4096

    beam = []

##    # naive version
##    # 49193 function calls in 0.772 CPU seconds
##    # 20234 function calls in 0.581 CPU seconds
##    for item in raw:
##        if item & flag:
##            beam.extend([0]* (item & data))
##        else:
##            beam.append(item & data)

    # performance version - hopefully
    # 6204 function calls in 0.149 CPU seconds

    # get all compression cases
    flagged = np.where(raw & flag)[0]

    # if there is no zero in the whole data, we can return raw as it is
    if flagged.size == 0:
        assert raw.size == 128
        return raw

    # everything until the first flag is normal data
    beam.extend(raw[0:flagged[0]])

    # iterate over all flags except the last one
    for this, next in zip(flagged[:-1],flagged[1:]):
        # create as many zeros as there are given within the flagged
        # byte's data part
        beam.extend([0]* (raw[this] & data))
        # append the data until the next flag
        beam.extend(raw[this+1:next])

    # process the last flag
    # add zeroes
    beam.extend([0]* (raw[flagged[-1]] & data))

    # add remaining data
    beam.extend(raw[flagged[-1]+1:])

    # return the data
    return np.array(beam)


def readDX(filename):
    r"""Data reader for German Weather Service DX raw radar data files
    developed by Thomas Pfaff.

    The algorith basically unpacks the zeroes and returns a regular array of
    360 x 128 data values.

    Parameters
    ----------
    filename : binary file of DX raw data

    Returns
    -------
    data : numpy array of image data [dBZ]; shape (360,128)

    attributes : dictionary of attributes - currently implemented keys:

        - 'azim' - azimuths np.array of shape (360,)
        - 'elev' - elevations (1 per azimuth); np.array of shape (360,)
        - 'clutter' - clutter mask; boolean array of same shape as `data`;
            corresponds to bit 15 set in each dataset.
    """

    azimuthbitmask = 2**(14-1)
    databitmask = 2**(13-1) - 1
    clutterflag = 2**15
    dataflag = 2**13 -1
    # open the DX file in binary mode for reading
    if type(filename) == file:
        f = filename
    else:
        f = open(filename, 'rb')

    # the static part of the DX Header is 68 bytes long
    # after that a variable message part is appended, which apparently can
    # become quite long. Therefore we do it the dynamic way.
    staticheadlen = 68
    statichead = f.read(staticheadlen)

    # find MS and extract following number
    msre = re.compile('MS([ 0-9]{3})')
    mslen = int(msre.search(statichead).group(1))
    # add to headlength and read that
    headlen = staticheadlen + mslen + 1

    # this is now our first header length guess
    # however, some files have an additional 0x03 byte after the first one
    # (older files or those from the Uni Hannover don't, newer have it, if
    # the header would end after an uneven number of bytes)
    #headlen = headend
    f.seek(headlen)
    # so we read one more byte
    void = f.read(1)
    # and check if this is also a 0x03 character
    if void == chr(3):
        headlen = headlen + 1

    # rewind the file
    f.seek(0)

    # read the actual header
    header = f.read(headlen)

    # we can interpret the rest directly as a 1-D array of 16 bit unsigned ints
    raw = np.fromfile(f, dtype='uint16')

    # reading finished, close file.
    f.close()

    # a new ray/beam starts with bit 14 set
    # careful! where always returns its results in a tuple, so in order to get
    # the indices we have to retrieve element 0 of this tuple
    newazimuths = np.where( raw == azimuthbitmask )[0]  ###Thomas kontaktieren!!!!!!!!!!!!!!!!!!!

    # for the following calculations it is necessary to have the end of the data
    # as the last index
    newazimuths = np.append(newazimuths,len(raw))

    # initialize our list of rays/beams
    beams = []
    # initialize our list of elevations
    elevs = []
    # initialize our list of azimuths
    azims = []

    # iterate over all beams
    for i in range(newazimuths.size-1):
        # unpack zeros
        beam = unpackDX(raw[newazimuths[i]+3:newazimuths[i+1]])
        # the beam may regularly only contain 128 bins, so we
        # explicitly cut that here to get a rectangular data array
        beams.append(beam[0:128])
        elevs.append((raw[newazimuths[i]+2] & databitmask)/10.)
        azims.append((raw[newazimuths[i]+1] & databitmask)/10.)

    beams = np.array(beams)

    attrs =  {}
    attrs['elev']  = np.array(elevs)
    attrs['azim'] = np.array(azims)
    attrs['clutter'] = (beams & clutterflag) != 0

    # converting the DWD rvp6-format into dBZ data and return as numpy array together with attributes
    return (beams & dataflag) * 0.5 - 32.5, attrs


def _write_polygon2txt(f, idx, vertices):
    f.write('%i %i\n'%idx)
    for i, vert in enumerate(vertices):
        f.write('%i '%(i,))
        f.write('%f %f %f %f\n' % tuple(vert))


def writePolygon2Text(fname, polygons):
    """Writes Polygons to a Text file which can be interpreted by ESRI \
    ArcGIS's "Create Features from Text File (Samples)" tool.

    This is (yet) only a convenience function with limited functionality.
    E.g. interior rings are not yet supported.

    Parameters
    ----------
    fname : string
        name of the file to save the vertex data to
    polygons : list of lists
        list of polygon vertices.
        Each vertex itself is a list of 3 coordinate values and an
        additional value. The third coordinate and the fourth value may be nan.

    Returns
    -------
    None

    Notes
    -----
    As Polygons are closed shapes, the first and the last vertex of each
    polygon **must** be the same!

    Examples
    --------
    Writes two triangle Polygons to a text file

    >>> poly1 = [[0.,0.,0.,0.],[0.,1.,0.,1.],[1.,1.,0.,2.],[0.,0.,0.,0.]]
    >>> poly2 = [[0.,0.,0.,0.],[0.,1.,0.,1.],[1.,1.,0.,2.],[0.,0.,0.,0.]]
    >>> polygons = [poly1, poly2]
    >>> writePolygon2Text('polygons.txt', polygons)

    The resulting text file will look like this::

        Polygon
        0 0
        0 0.000000 0.000000 0.000000 0.000000
        1 0.000000 1.000000 0.000000 1.000000
        2 1.000000 1.000000 0.000000 2.000000
        3 0.000000 0.000000 0.000000 0.000000
        1 0
        0 0.000000 0.000000 0.000000 0.000000
        1 0.000000 1.000000 0.000000 1.000000
        2 1.000000 1.000000 0.000000 2.000000
        3 0.000000 0.000000 0.000000 0.000000
        END

    """
    with open(fname, 'w') as f:
        f.write('Polygon\n')
        count = 0
        for vertices in polygons:
            _write_polygon2txt(f, (count, 0), vertices)
            count += 1
        f.write('END\n')


def read_EDGE_netcdf(filename, range_lim = 200000., enforce_equidist=False):
    """Data reader for netCDF files exported by the EDGE radar software

    The corresponding NetCDF files from the EDGE software typically contain only
    one variable (e.g. reflectivity) for one elevation angle (sweep). The elevation
    angle is specified in the attributes keyword "Elevation".

    Please note that the radar might not return data with equidistant azimuth angles.
    In case you need equidistant azimuth angles, please set enforce_equidist to True.

    Parameters
    ----------
    filename : path of the netCDF file
    range_lim : range limitation [m] of the returned radar data
                (200000 per default)
    enforce_equidist : boolean
        Set True if the values of the azimuth angles should be forced to be equidistant
        default value is False

    Returns
    -------
    output : numpy array of image data (dBZ), dictionary of attributes

    """
    # read the data from file
    dset = nc.Dataset(filename)
    data = dset.variables[dset.TypeName][:]
    # Check azimuth angles and rotate image
    az = dset.variables['Azimuth'][:]
    # These are the indices of the minimum and maximum azimuth angle
    ix_minaz = np.argmin(az)
    ix_maxaz = np.argmax(az)
    if enforce_equidist:
        az = np.linspace(np.round(az[ix_minaz],2), np.round(az[ix_maxaz],2), len(az))
    else:
        az = np.roll(az, -ix_minaz)
    # rotate accordingly
    data = np.roll(data, -ix_minaz, axis=0)
    data = np.where(data==dset.getncattr('MissingData'), np.nan, data)
    # Ranges
    binwidth = (dset.getncattr('MaximumRange-value') * 1000.) / len(dset.dimensions['Gate'])
    r = np.arange(binwidth, (dset.getncattr('MaximumRange-value') * 1000.) + binwidth, binwidth)
    # collect attributes
    attrs =  {}
    for attrname in dset.ncattrs():
        attrs[attrname] = dset.getncattr(attrname)
    # Limiting the returned range
    if range_lim and range_lim / binwidth <= data.shape[1]:
        data = data[:,:range_lim / binwidth]
        r = r[:range_lim / binwidth]
    # Set additional metadata attributes
    attrs['az'] = az
    attrs['r']  = r
    attrs['sitecoords'] = (attrs['Latitude'], attrs['Longitude'], attrs['Height'])
    attrs['time'] = dt.datetime.utcfromtimestamp(attrs.pop('Time'))
    attrs['max_range'] = data.shape[1] * binwidth
    dset.close()

    return data, attrs


def read_BUFR(buffile):
    """Main BUFR interface: Decodes BUFR file and returns metadata and values

    The actual function refererence is contained in :doc:`wradlib.bufr.decodebufr`.

    """
    return bufr.decodebufr(buffile)


def parse_DWD_quant_composite_header(header):
    """Parses the ASCII header of a DWD quantitative composite file

    Parameters
    ----------
    header : string (ASCII header)

    Returns
    -------
    output : dictionary of metadata retreived from file header

    """
    # empty container
    out = {}
    # RADOLAN product type def
    out["producttype"] = header[0:2]
    # file time stamp as Python datetime object
    out["datetime"] = dt.datetime.strptime(header[2:8]+header[13:17]+"00", "%d%H%M%y%m%S")
    # radar location ID (always 10000 for composites)
    out["radarid"] = header[8:13]
    pos_VS = header.find("VS")
    pos_SW = header.find("SW")
    pos_PR = header.find("PR")
    pos_INT = header.find("INT")
    pos_GP = header.find("GP")
    pos_MS = header.find("MS")
    if pos_VS > -1:
        out["maxrange"] = {0:"100 km and 128 km (mixed)", 1: "100 km", 2:"128 km" }[int(header[(pos_VS+2):pos_SW])]
    else:
        out["maxrange"] = "100 km"
    out["radolanversion"] = header[(pos_SW+2):pos_PR]
    out["intervalseconds"] = int(header[(pos_INT+3):pos_GP])*60
    dimstrings = header[(pos_GP+2):pos_MS].strip().split("x")
    out["nrow"] = int(dimstrings[0])
    out["ncol"] = int(dimstrings[1])
    locationstring = header[(pos_MS+2):].strip().split("<")[1].strip().strip(">")
    out["radarlocations"] = locationstring.split(",")
    return out


def read_RADOLAN_composite(fname):
    """Read quantitative radar composite format of the German Weather Service

    The quantitative composite format of the DWD (German Weather Service) was
    established in the course of the `RADOLAN project <http://www.dwd.de/radolan>`
    and includes several file types, e.g. RX, RO, RK, RZ, RP, RT, RC, RI, RG and
    many, many more (see format description on the project homepage, [DWD2009).

    At the moment, the national RADOLAN composite is a 900 x 900 grid with 1 km
    resolution and in polar-stereographic projection.

    Parameters
    ----------
    fname : path to the composite file

    Returns
    -------
    output : tuple of two items (data, attrs)
        - data : numpy array of shape (number of rows, number of columns)
        - attrs : dictionary of metadata information from the file header

    References
    ----------

    .. [DWD2009] Germany Weather Service (DWD), 2009: RADLOAN/RADVO-OP -
        Beschreibung des Kompositformats, Version 2.2.1. Offenbach, Germany,
        URL: http://dwd.de/radolan (in German)

    """
    result = []
    mask = 4095 # max value integer
    NODATA = -9999
    header = '' # header string for later processing
    # open file handle
    f = open(fname, 'rb')
    # read header
    while True :
        mychar = f.read(1)
        if mychar == chr(3) :
            break
        header = header + mychar
    attrs = parse_DWD_quant_composite_header(header)
    attrs["nodataflag"] = -9999
    if not attrs["radarid"]=="10000":
        print "WARNING: You are using this function for a non composite file"
        print "It might work...but please check the validity of the results"
    if attrs["producttype"] == "RX":
        # NOT TESTED, YET
        # read the actual data
        indat = f.read(attrs["nrow"]*attrs["ncol"])
        # convert to 16-bit integers
        arr = np.frombuffer(indat, np.int8)
        arr = np.where(arr==250,NODATA,arr)
        clutter = np.where(arr==249)[0]
    else:
        # read the actual data
        indat = f.read(attrs["nrow"]*attrs["ncol"]*2)
        # convert to 16-bit integers
        arr = np.frombuffer(indat, np.int16)
        # evaluate bits 14, 15 and 16
        nodata   = np.where(arr & int("10000000000000",2))
        negative = np.where(arr & int("100000000000000",2))
        clutter  = np.where(arr & int("1000000000000000",2))
        # mask out the last 4 bits
        arr = arr & mask
        # consider negative flag if product is RD (differences from adjustment)
        if attrs["producttype"]=="RD":
            # NOT TESTED, YET
            arr[negative] = -arr[negative]
        # convert no data to NaN
        ### This is the old way
        ##arr = np.where(arr==2500,np.nan,arr)
        arr[nodata] = NODATA
    # bring it into shape
    arr = arr.reshape( (attrs["nrow"], attrs["ncol"]) )
##    arr = np.flipud(arr)
    # append clutter mask
    attrs['cluttermask'] = clutter

    return arr, attrs

def browse_hdf5_group(grp):
    """Browses one hdf5 file level
    """
    pass

def read_generic_hdf5(fname):
    """Reads hdf5 files according to their structure

    In contrast to other file readers under wradlib.io, this function will *not* return
    a two item tuple with (data, metadata). Instead, this function returns ONE
    dictionary that contains all the file contents - both data and metadata. The keys
    of the output dictionary conform to the Group/Subgroup directory branches of
    the original file.

    Parameters
    ----------
    fname : string (a hdf5 file path)

    Returns
    -------
    output : a dictionary that contains both data and metadata according to the
              original hdf5 file structure
    
    """
    f = h5py.File(fname, "r")
    fcontent = {}
    def filldict(x, y):
        # create a new container
        tmp = {}
        # add attributes if present
        if len(y.attrs) > 0:
            tmp['attrs'] = dict(y.attrs)
        # add data if it is a dataset
        if isinstance(y, h5py.Dataset):
            tmp['data'] = np.array(y)
        # only add to the dictionary, if we have something meaningful to add
        if tmp != {}:
            fcontent[x] = tmp
    f.visititems(filldict)

    f.close()

    return fcontent
    
def read_OPERA_hdf5(fname):
    """Reads hdf5 files according to OPERA conventions

    Please refer to the `OPERA data model documentation <http://www.knmi.nl/opera/opera3/OPERA_2008_03_WP2.1b_ODIM_H5_v2.1.pdf>`_
    in order to understand how an hdf5 file is organized that conforms to the OPERA
    ODIM_H5 conventions.

    In contrast to other file readers under wradlib.io, this function will *not* return
    a two item tuple with (data, metadata). Instead, this function returns ONE
    dictionary that contains all the file contents - both data and metadata. The keys
    of the output dictionary conform to the Group/Subgroup directory branches of
    the original file. If the end member of a branch (or path) is "data", then the
    corresponding item of output dictionary is a numpy array with actual data. Any other
    end member (either *how*, *where*, and *what*) will contain the meta information
    applying to the coresponding level of the file hierarchy.

    Parameters
    ----------
    fname : string (a hdf5 file path)

    Returns
    -------
    output : a dictionary that contains both data and metadata according to the
              original hdf5 file structure

    """
    f = h5py.File(fname, "r")
    # try verify OPERA conventions
##    if not f.keys() == ['dataset1', 'how', 'what', 'where']:
##        print "File is not organized according to OPERA conventions (ODIM_H5)..."
##        print "Expected the upper level subgroups to be: dataset1, how, what', where"
##        print "Try to use e.g. ViTables software in order to inspect the file hierarchy."
##        sys.exit(1)

    # now we browse through all Groups and Datasets and store the info in one dictionary
    fcontent = {}
    def filldict(x, y):
        if isinstance(y, h5py.Group):
            if len(y.attrs) > 0:
                fcontent[x] = dict(y.attrs)
        elif isinstance(y, h5py.Dataset):
            fcontent[x] = np.array(y)
    f.visititems(filldict)

    f.close()

    return fcontent


def read_gamic_scan_attributes(scan, scan_type, range_lim):
    """Read attributes from one particular scan from a GAMIC hdf5 file

    Provided by courtesy of Kai Muehlbauer (University of Bonn).

    Parameters
    ----------
    scan : scan object from hdf5 file
    scan_type : string
        "PPI" (plain position indicator) or "RHI" (radial height indicator)
    range_lim : float
        range limitation (meters) of the returned radar data

    Returns
    -------
    sattrs  : dictionary of scan attributes

    """

    # placeholder for attributes
    sattrs = {}

    # link to scans 'how' hdf5 group
    sg1 = scan['how']

    # get scan attributes
    for attrname in list(sg1.attrs):
        sattrs[attrname] = sg1.attrs.get(attrname)
    sattrs['bin_range'] = sattrs['range_step'] * sattrs['range_samples']

    # get scan header
    ray_header = scan['ray_header']

    # az, el, zero_index for PPI scans
    if scan_type == 'PVOL':
        azi_start = ray_header['azimuth_start']
        azi_stop = ray_header['azimuth_stop']
         # Azimuth corresponding to 1st ray
        zero_index = np.where(azi_stop < azi_start)
        azi_stop[zero_index[0]] += 360
        zero_index = zero_index[0] + 1
        az = (azi_start+azi_stop)/2
        az = np.roll(az,-zero_index, axis=0)
        az = np.round(az, 1)
        el = sg1.attrs.get('elevation')

    # az, el, zero_index for RHI scans
    if scan_type == 'RHI':
        ele_start = ray_header['elevation_start']
        ele_stop = ray_header['elevation_stop']
        # Elevation corresponding to 1st ray
        zero_index = np.where(ele_stop > ele_start)
        zero_index = zero_index[0] - 1
        el = (ele_start+ele_stop)/2
        el = np.round(el, 1)
        el = el[zero_index[0]:]
        az = sg1.attrs.get('azimuth')

    # save zero_index (first ray) to scan attributes
    sattrs['zero_index'] = zero_index[0]

    # create range array
    r = np.arange(sattrs['bin_range'], sattrs['bin_range']*sattrs['bin_count']+sattrs['bin_range'], sattrs['bin_range'])
    if range_lim and range_lim / sattrs['bin_range'] <= r.shape[0]:
        r = r[:range_lim / sattrs['bin_range']]

    # save variables to scan attributes
    sattrs['az'] = az
    sattrs['el'] = el
    sattrs['r']  = r
    sattrs['Time'] = sattrs.pop('timestamp')
    sattrs['max_range'] = r[-1]

    return sattrs


def read_gamic_scan(scan, scan_type, wanted_moments, range_lim):
    """Read data from one particular scan from GAMIC hdf5 file

    Provided by courtesy of Kai Muehlbauer (University of Bonn).

    Parameters
    ----------
    scan : scan object from hdf5 file
    scan_type : string
        "PPI" (plain position indicator) or "RHI" (radial height indicator)
    wanted_moments  : sequence of strings containing upper case names of moment to be returned
    range_lim : float
        range limitation (meters) of the returned radar data

    Returns
    -------
    data : dictionary of moment data (numpy arrays)
    sattrs : dictionary of scan attributes

    """


    # placeholder for data and attrs
    data = {}
    sattrs =  {}

    # try to read wanted moments
    for mom in list(scan):
        if 'moment' in mom:
            sg2 = scan[mom]
            #sg2_attr = list(sg2.attrs)
            #print(sg2_attr)
            actual_moment = sg2.attrs.get('moment').upper()
            if actual_moment in wanted_moments or wanted_moments == 'all':
                # read attributes only once
                if not sattrs:
                    sattrs = read_gamic_scan_attributes(scan, scan_type, range_lim)
                mdata = sg2[...]
                #print(data.size)
                dyn_range_max = sg2.attrs.get('dyn_range_max')
                dyn_range_min = sg2.attrs.get('dyn_range_min')
                bin_format = sg2.attrs.get('format')
                if bin_format == 'UV8':
                    div = 256.0
                else:
                    div = 65536.0
                mdata = dyn_range_min + mdata*(dyn_range_max-dyn_range_min)/div

                if scan_type == 'PVOL':
                    # rotate accordingly
                    mdata = np.roll(mdata,-1 * sattrs['zero_index'], axis=0)

                if scan_type == 'RHI':
                    # remove first zero angles
                    mdata = mdata[sattrs['zero_index']:,:]

                # Limiting the returned range according to range_lim
                if range_lim and range_lim / sattrs['bin_range'] <= mdata.shape[1]:
                    mdata = mdata[:,:range_lim / sattrs['bin_range']]

                data[actual_moment] = mdata
                #data.append(mdata)

    return data, sattrs


def read_GAMIC_hdf5(filename, range_lim = 100000., wanted_elevations = '1.5', wanted_moments = 'UH'):
    """Data reader for hdf5 files produced by the commercial GAMIC Enigma V3 MURAN software

    Provided by courtesy of Kai Muehlbauer (University of Bonn). See GAMIC
    homepage for further info (http://www.gamic.com/cgi-bin/info.pl?link=softwarebrowser3).

    Parameters
    ----------
    filename : path of the gamic hdf5 file
    scan_type : string
        "PPI" (plain position indicator) or "RHI" (radial height indicator)
    range_lim : float
        range limitation (meters) of the returned radar data (100000. by default)
    elevation_angle : sequence of strings of elevation_angle(s) of scan (only needed for PPI)
    moments : sequence of strings of moment name(s)

    Returns
    -------
    data : dictionary of scan and moment data (numpy arrays)
    attrs : dictionary of attributes

    """

    # read the data from file
    f = h5py.File(filename,'r')

    # placeholder for attributes and data
    attrs =  {}
    vattrs = {}
    data = {}

    #get scan_type (PVOL or RHI)
    scan_type = f['what'].attrs.get('object')

    # single or volume scan
    if scan_type == 'PVOL':
        # loop over 'main' hdf5 groups (how, scanX, what, where)
        for n in list(f):
            if 'scan' in n:
                g = f[n]
                sg1 = g['how']

                # get scan elevation
                el = sg1.attrs.get('elevation')
                el = str(round(el,2))

                # try to read scan data and attrs if wanted elevations are found
                if (el in wanted_elevations) or (wanted_elevations == 'all'):
                    sdata, sattrs = read_gamic_scan(scan = g, scan_type = scan_type, wanted_moments = wanted_moments, range_lim = range_lim)
                    if sdata:
                        data[n.upper()] = sdata
                    if sattrs:
                        attrs[n.upper()] = sattrs

    # single rhi scan
    elif scan_type == 'RHI':
        # loop over 'main' hdf5 groups (how, scanX, what, where)
	for n in list(f):
            if 'scan' in n:
                g = f[n]
                # try to read scan data and attrs
                sdata, sattrs = read_gamic_scan(scan = g, scan_type = scan_type, wanted_moments = wanted_moments, range_lim = range_lim)
                if sdata:
                    data[n.upper()] = sdata
                if sattrs:
                    attrs[n.upper()] = sattrs

    # collect volume attributes if wanted data is available
    if data:
        vattrs['Latitude'] = f['where'].attrs.get('lat')
        vattrs['Longitude'] = f['where'].attrs.get('lon')
        vattrs['Height'] = f['where'].attrs.get('height')
        # check wether its useful to implement that feature
        #vattrs['sitecoords'] = (vattrs['Latitude'], vattrs['Longitude'], vattrs['Height'])
        attrs['VOL'] = vattrs

    f.close()

    return data, attrs


def to_pickle(fpath, obj):
    """Pickle object <obj> to file <fpath>
    """
    output = open(fpath, 'wb')
    pickle.dump(obj, output)
    output.close()


def from_pickle(fpath):
    """Return pickled object from file <fpath>
    """
    pkl_file = open(fpath, 'rb')
    obj = pickle.load(pkl_file)
    pkl_file.close()
    return obj


def to_hdf5(fpath, data, metadata={}, dataset="data", compression="gzip"):
    """Quick storage of one <data> array and a <metadata> dict in an hdf5 file

    This is more efficient than pickle, cPickle or numpy.save. The data is stored in
    a subgroup named ``data`` (i.e. hdf5file["data").

    Parameters
    ----------
    fpath : string (path to the hdf5 file)
    data : numpy array
    metadata : dictionary
    dtype : a numpy dtype string
    compression : h5py comression type {"gzip"|"szip"|"lzf"}, see h5py documentation for details

    """
    f = h5py.File(fpath, mode="w")
    dset = f.create_dataset(dataset, data=data, compression=compression)
    # store metadata
    for key in metadata.keys():
        dset.attrs[key] = metadata[key]
    # close hdf5 file
    f.close()


def from_hdf5(fpath, dataset="data"):
    """Loading data from hdf5 files that was stored by <wradlib.io.to_hdf5>

    Parameters
    ----------
    fpath : string (path to the hdf5 file)
    dataset : name of the Dataset in which the data is stored

    """
    f = h5py.File(fpath, mode="r")
    # Check whether Dataset exists
    if not dataset in f.keys():
        print("Cannot read Dataset <%s> from hdf5 file <%s>" % (dataset, f))
        f.close()
        sys.exit()
    data = np.array(f[dataset][:])
    # get metadata
    metadata = {}
    for key in f[dataset].attrs.keys():
        metadata[key] = f[dataset].attrs[key]
    f.close()
    return data, metadata


if __name__ == '__main__':
    print 'wradlib: Calling module <io> as main...'
