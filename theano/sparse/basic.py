"""Classes for handling sparse matrices.

To read about different sparse formats, see
http://www-users.cs.umn.edu/~saad/software/SPARSKIT/paper.ps
"""

# TODO
# Automatic methods for determining best sparse format?

from itertools import izip
import sys

import numpy
import theano
import scipy.sparse

from theano import gof, tensor, compile, scalar, config
from theano.gof.python25 import all
from theano.tensor import blas
from theano.sparse.utils import hash_from_sparse
import theano.tests.unittest_tools as utt

sparse_formats = ['csc', 'csr']


#TODO: move this decorator to the compile submodule
def register_specialize(lopt, *tags, **kwargs):
    compile.optdb['specialize'].register((kwargs and kwargs.pop('name')) or
                                         lopt.__name__, lopt, 'fast_run',
                                         *tags)

""" Types of sparse matrices to use for testing """
_mtypes = [scipy.sparse.csc_matrix, scipy.sparse.csr_matrix]
#_mtypes = [sparse.csc_matrix, sparse.csr_matrix, sparse.dok_matrix,
# sparse.lil_matrix, sparse.coo_matrix]
#* new class ``dia_matrix`` : the sparse DIAgonal format
#* new class ``bsr_matrix`` : the Block CSR format
_mtype_to_str = {scipy.sparse.csc_matrix: "csc",
                 scipy.sparse.csr_matrix: "csr"}


def _is_sparse_variable(x):
    """
    @rtype: boolean
    @return: True iff x is a L{SparseVariable} (and not a L{tensor.TensorType})
    """
    if not isinstance(x.type, (SparseType, tensor.TensorType)):
        raise NotImplementedError("this function should only be called on "
                                  "*variables* (of type sparse.SparseType "
                                  "or tensor.TensorType), not,", x)
    return isinstance(x.type, SparseType)


def _is_dense_variable(x):
    """
    @rtype: boolean
    @return: True unless x is a L{SparseVariable} (and not a
    L{tensor.TensorType})
    """
    if not isinstance(x.type, (SparseType, tensor.TensorType)):
        raise NotImplementedError("this function should only be called on "
                                  "*variables* (of type sparse.SparseType or "
                                  "tensor.TensorType), not,", x)
    return isinstance(x.type, tensor.TensorType)


def _is_sparse(x):
    """
    @rtype: boolean
    @return: True iff x is a L{scipy.sparse.spmatrix} (and not a
    L{numpy.ndarray})
    """
    if not isinstance(x, (scipy.sparse.spmatrix, numpy.ndarray)):
        raise NotImplementedError("this function should only be called on "
                                  "sparse.scipy.sparse.spmatrix or "
                                  "numpy.ndarray, not,", x)
    return isinstance(x, scipy.sparse.spmatrix)


def _is_dense(x):
    """
    @rtype: boolean
    @return: True unless x is a L{scipy.sparse.spmatrix} (and not a
    L{numpy.ndarray})
    """
    if not isinstance(x, (scipy.sparse.spmatrix, numpy.ndarray)):
        raise NotImplementedError("this function should only be called on "
                                  "sparse.scipy.sparse.spmatrix or "
                                  "numpy.ndarray, not,", x)
    return isinstance(x, numpy.ndarray)


def _kmap_eq(a, b):
    if a is None and b is None:
        return True
    return numpy.all(a == b)


def _kmap_hash(a):
    if a is None:
        return 12345
    return hash(numpy.str(a))


# Wrapper type
def as_sparse_variable(x, name=None):
    """Wrapper around SparseVariable constructor to construct
    a Variable with a sparse matrix with the same dtype and
    format.

    :param x: A sparse matrix.

    :return: SparseVariable version of `x`.
    """

    # TODO
    # Verify that sp is sufficiently sparse, and raise a
    # warning if it is not

    if isinstance(x, gof.Apply):
        if len(x.outputs) != 1:
            raise ValueError("It is ambiguous which output of a "
                             "multi-output Op has to be fetched.", x)
        else:
            x = x.outputs[0]
    if isinstance(x, gof.Variable):
        if not isinstance(x.type, SparseType):
            raise TypeError("Variable type field must be a SparseType.", x,
                            x.type)
        return x
    try:
        return constant(x, name=name)
    except TypeError:
        raise TypeError("Cannot convert %s to SparseType" % x, type(x))
as_sparse = as_sparse_variable


def as_sparse_or_tensor_variable(x, name=None):
    """Same as `as_sparse_variable` but If we can't make a
    sparse variable, we try to make a tensor variable.
    format.

    :param x: A sparse matrix.

    :return: SparseVariable or TensorVariable version of `x`.
    """

    try:
        return as_sparse_variable(x, name)
    except (ValueError, TypeError):
        return theano.tensor.as_tensor_variable(x, name)


def verify_grad_sparse(op, pt, structured=False, *args, **kwargs):
    """Wrapper for theano.test.unittest_tools.py:verify_grad wich
    converts sparse variables back and forth.

    :param op: Op to check.
    :param pt: List of inputs to realize the tests.
    :param structured: True to tests with a structured grad,
                       False otherwise.
    :param *args: Other `verify_grad` parameters if any.
    :param **kwargs: Other `verify_grad` keywords if any.

    :return: None
    """

    conv_none = lambda x: x

    def conv_csr(ind, indptr, shp):
        def f(spdata):
            return CSR(spdata, ind, indptr, shp)
        return f

    def conv_csc(ind, indptr, shp):
        def f(spdata):
            return CSC(spdata, ind, indptr, shp)
        return f

    iconv = []
    dpt = []

    for p in pt:
        if _is_sparse(p):
            if structured:
                dpt.append(p.data)
            else:
                dpt.append(p.toarray())
            if p.format == 'csr':
                if structured:
                    iconv.append(conv_csr(p.indices[:p.size], p.indptr,
                                          p.shape))
                else:
                    iconv.append(csr_from_dense)
            elif p.format == 'csc':
                if structured:
                    iconv.append(conv_csc(p.indices[:p.size], p.indptr,
                                          p.shape))
                else:
                    iconv.append(csc_from_dense)
            else:
                raise NotImplementedError("No conv for %s" % (p.format,))
        else:
            dpt.append(p)
            iconv.append(conv_none)
    output = op(*[as_sparse_or_tensor_variable(p) for p in pt])
    if isinstance(output, (list, tuple)):
        raise NotImplementedError("verify_grad can't deal with "
                                  "multiple outputs")
    if _is_sparse_variable(output):
        oconv = DenseFromSparse(structured=structured)
    else:
        oconv = conv_none

    def conv_op(*inputs):
        ipt = [conv(i) for i, conv in zip(inputs, iconv)]
        out = op(*ipt)
        return oconv(out)

    return utt.verify_grad(conv_op, dpt, *args, **kwargs)
verify_grad_sparse.E_grad = utt.verify_grad.E_grad


def constant(x, name=None):
    if not isinstance(x, scipy.sparse.spmatrix):
        raise TypeError("sparse.constant must be called on a "
                        "scipy.sparse.spmatrix")
    try:
        return SparseConstant(SparseType(format=x.format,
                                         dtype=x.dtype), x.copy(), name=name)
    except TypeError:
        raise TypeError("Could not convert %s to SparseType" % x, type(x))


def sp_ones_like(x):
    # TODO: don't restrict to CSM formats
    data, indices, indptr, shape = csm_properties(x)
    return CSM(format=x.format)(tensor.ones_like(data), indices, indptr, shape)


def sp_zeros_like(x):
    #TODO: don't restrict to CSM formats
    _, _, indptr, shape = csm_properties(x)
    return CSM(format=x.format)(numpy.array([], dtype=x.type.dtype),
                                numpy.array([]), tensor.zeros_like(indptr),
                                shape)


class _sparse_py_operators:
    T = property(lambda self: transpose(self),
                 doc="Return aliased transpose of self (read-only)")

    def __neg__(self):
        return neg(self)

    def __add__(left, right):
        return add(left, right)

    def __radd__(right, left):
        return add(left, right)

    def __sub__(left, right):
        return sub(left, right)

    def __rsub__(right, left):
        return sub(left, right)

    def __mul__(left, right):
        return mul(left, right)

    def __rmul__(left, right):
        return mul(left, right)

    #extra pseudo-operator symbols

    def __dot__(left, right):
        return structured_dot(left, right)

    def __rdot__(right, left):
        return structured_dot(left, right)

    #N.B. THIS IS COMMENTED OUT ON PURPOSE!!!
    #     Discussion with Fred & James (at least, and maybe others before)
    #     we decided that casting from a sparse to dense should be explicit
    #     because it's usually something you just want to be pretty careful
    #     about, and not to do by accident.
    #def _as_TensorVariable(self):
    #    return dense_from_sparse(self)

    shape = property(lambda self: tensor.shape(dense_from_sparse(self)))
    # don't worry!
    # the plan is that the ShapeFeature in tensor.opt will do shape propagation
    # and remove the dense_from_sparse from the graph.  This will *NOT*
    # actually expand your sparse matrix just to get the shape.
    ndim = property(lambda self: self.type.ndim)
    dtype = property(lambda self: self.type.dtype)

    # Note that the `size` attribute of sparse matrices behaves differently
    # from dense matrices: it is the number of elements stored in the matrix
    # rather than the total number of elements that may be stored. Note also
    # that stored zeros *do* count in the size.
    size = property(lambda self: csm_data(self).size)

    def zeros_like(model):
        return sp_zeros_like(model)

    def __getitem__(self, args):
        if not isinstance(args, tuple):
            args = args,

        if len(args) == 2:
            scalar_arg_1 = (numpy.isscalar(args[0]) or
                            getattr(args[0], 'type', None) == tensor.iscalar)
            scalar_arg_2 = (numpy.isscalar(args[1]) or
                            getattr(args[1], 'type', None) == tensor.iscalar)
            if scalar_arg_1 and scalar_arg_2:
                ret = get_item_scalar(self, args)
            else:
                ret = get_item_2d(self, args)
        else:
            ret = get_item_2d(self, args)
        return ret


class SparseVariable(gof.Variable, _sparse_py_operators):
    dtype = property(lambda self: self.type.dtype)
    format = property(lambda self: self.type.format)

    def __str__(self):
        return '%s{%s,%s}' % (
                self.__class__.__name__,
                self.format,
                self.dtype)

    def __repr__(self):
        return str(self)


class SparseConstantSignature(tuple):
    def __eq__(self, other):
        (a, b), (x, y) = self, other
        return a == x \
                and (b.dtype == y.dtype)\
                and (type(b) == type(y))\
                and (b.shape == y.shape)\
                and (abs(b - y).sum() < 1e-6 * b.nnz)

    def __hash__(self):
        (a, b) = self
        return hash(type(self)) ^ hash(a) ^ hash(type(b))

    def theano_hash(self):
        (_, d) = self
        return hash_from_sparse(d)


class SparseConstant(gof.Constant, _sparse_py_operators):
    dtype = property(lambda self: self.type.dtype)
    format = property(lambda self: self.type.format)

    def signature(self):
        assert self.data is not None
        return SparseConstantSignature((self.type, self.data))

    def __str__(self):
        return '%s{%s,%s,shape=%s,nnz=%s}' % (
                self.__class__.__name__,
                self.format,
                self.dtype,
                self.data.shape,
                self.data.nnz)

    def __repr__(self):
        return str(self)


class SparseType(gof.Type):
    """
    @type dtype: numpy dtype string such as 'int64' or 'float64' (among others)
    @type format: string
    @ivar format: The sparse storage strategy.

    @note As far as I can tell, L{scipy.sparse} objects must be matrices, i.e.
    have dimension 2.
    """
    format_cls = {'csr': scipy.sparse.csr_matrix,
                  'csc': scipy.sparse.csc_matrix}
    dtype_set = set(['int8', 'int16', 'int32', 'int64', 'float32',
                     'float64', 'complex64', 'complex128'])
    ndim = 2

    Variable = SparseVariable
    Constant = SparseConstant

    def __init__(self, format, dtype):
        """
        Fundamental way to create a sparse node.
        @param dtype:   Type of numbers in the matrix.
        @param format:  The sparse storage strategy.
        @return         An empty SparseVariable instance.
        """
        dtype = str(dtype)
        if dtype in self.dtype_set:
            self.dtype = dtype
        else:
            raise NotImplementedError('unsupported dtype "%s" not in list' %
                                      dtype, list(self.dtype_set))

        assert isinstance(format, basestring)
        if format in self.format_cls:
            self.format = format
        else:
            raise NotImplementedError('unsupported format "%s" not in list' %
                                      format, self.format_cls.keys())

    def filter(self, value, strict=False, allow_downcast=None):
        if isinstance(value, self.format_cls[self.format])\
                and value.dtype == self.dtype:
            return value
        if strict:
            raise TypeError("%s is not sparse, or not the right dtype (is %s, "
                            "expected %s)" % (value, value.dtype, self.dtype))
        #The input format could be converted here
        if allow_downcast:
            sp = self.format_cls[self.format](value, dtype=self.dtype)
        else:
            sp = self.format_cls[self.format](value)
            if str(sp.dtype) != self.dtype:
                raise NotImplementedError("Expected %s dtype but got %s" %
                                          (self.dtype, str(sp.dtype)))
        if sp.format != self.format:
            raise NotImplementedError()
        return sp

    @staticmethod
    def may_share_memory(a, b):
        # This is Fred suggestion for a quick and dirty way of checking
        # aliasing .. this can potentially be further refined (ticket #374)
        if _is_sparse(a) and _is_sparse(b):
            return a is b
        if _is_sparse(b) and isinstance(a, numpy.ndarray):
            a, b = b, a
        if _is_sparse(a) and isinstance(b, numpy.ndarray):
            if (numpy.may_share_memory(a.data, b) or
                numpy.may_share_memory(a.indices, b) or
                numpy.may_share_memory(a.indptr, b)):
                # currently we can't share memory with a.shape as it is a tuple
                return True
        return False

    def make_variable(self, name=None):
        return SparseVariable(self, name=name)

    def __eq__(self, other):
        return (type(self) == type(other) and other.dtype == self.dtype and
                other.format == self.format)

    def __hash__(self):
        return hash(self.dtype) ^ hash(self.format)

    def __str__(self):
        return "Sparse[%s, %s]" % (str(self.dtype), str(self.format))

    def __repr__(self):
        return "Sparse[%s, %s]" % (str(self.dtype), str(self.format))

    def values_eq_approx(self, a, b, eps=1e-6):
        #WARNING: equality comparison of sparse matrices is not fast or easy
        # we definitely do not want to be doing this un-necessarily during
        # a FAST_RUN computation..
        if not scipy.sparse.issparse(a) or not scipy.sparse.issparse(b):
            return False
        diff = abs(a - b)
        if diff.nnz == 0:
            return True
        # Built-in max from python is not implemented for sparse matrix as a
        # reduction. It returns a sparse matrix wich cannot be compared to a
        # scalar. When comparing sparse to scalar, no exceptions is raised and
        # the returning value is not consistent. That is why it is apply to a
        # numpy.ndarray.
        return max(diff.data) < eps

    def values_eq(self, a, b):
        #WARNING: equality comparison of sparse matrices is not fast or easy
        # we definitely do not want to be doing this un-necessarily during
        # a FAST_RUN computation..
        return scipy.sparse.issparse(a) \
                and scipy.sparse.issparse(b) \
                and abs(a - b).sum() == 0.0

    def is_valid_value(self, a):
        return scipy.sparse.issparse(a) and (a.format == self.format)

# Register CudaNdarrayType to the OutputGuard list of known types
# to have OutputGuard generate C code for this type.
theano.compile.mode.register_OutputGuard_c_code(SparseType)


# for more dtypes, call SparseType(format, dtype)
def matrix(format, name=None, dtype=None):
    if dtype is None:
        dtype = config.floatX
    type = SparseType(format=format, dtype=dtype)
    return type(name)


def csc_matrix(name=None, dtype=None):
    return matrix('csc', name, dtype)


def csr_matrix(name=None, dtype=None):
    return matrix('csr', name, dtype)


# for more dtypes, call SparseType(format, dtype)
csc_dmatrix = SparseType(format='csc', dtype='float64')
csr_dmatrix = SparseType(format='csr', dtype='float64')
csc_fmatrix = SparseType(format='csc', dtype='float32')
csr_fmatrix = SparseType(format='csr', dtype='float32')

all_dtypes = SparseType.dtype_set
complex_dtypes = [t for t in all_dtypes if t[:7] == 'complex']
float_dtypes = [t for t in all_dtypes if t[:5] == 'float']
int_dtypes = [t for t in all_dtypes if t[:3] == 'int']
uint_dtypes = [t for t in all_dtypes if t[:4] == 'uint']

continuous_dtypes = complex_dtypes + float_dtypes
discrete_dtypes = int_dtypes + uint_dtypes


# CONSTRUCTION
class CSMProperties(gof.Op):
    """Extract all of .data, .indices, .indptr and .shape.

    For specific field, `csm_data`, `csm_indices`, `csm_indptr`
    and `csm_shape` are provided. Also, `kmap` could be
    set through to constructor to specified the parts
    of the parameter `data` the op should return.Fancy indexing
    with numpy.ndarray should be used for this purpose.

    :param csm: Sparse matrix in CSR or CSC format.

    :return: (data, indices, indptr, shape), the properties
             of `csm`.

    :note:
    - The grad implemented is regular, i.e. not structured.
    - `infer_shape` method is not available for this op.
    """

    # NOTE
    # We won't implement infer_shape for this op now. This will
    # ask that we implement an GetNNZ op, and this op will keep
    # the dependence on the input of this op. So this won't help
    # to remove computations in the graph. To remove computation,
    # we will need to make an infer_sparse_pattern feature to
    # remove computations. Doing this is trickier then the
    # infer_shape feature. For example, how do we handle the case
    # when some op create some 0 values? So there is dependence
    # on the values themselves. We could write an infer_shape for
    # the last output that is the shape, but I dough this will
    # get used.

    # we don't return a view of the shape, we create a new ndarray from the
    # shape tuple.
    view_map = {0: [0], 1: [0], 2: [0]}

    kmap = None
    """Indexing to speficied what part of the data parameter
    should be use to construct the sparse matrix."""

    def __init__(self, kmap=None):
        self.kmap = kmap

    def __eq__(self, other):
        return type(self) == type(other) and _kmap_eq(self.kmap, other.kmap)

    def __hash__(self):
        return 8234 ^ hash(type(self)) ^ _kmap_hash(self.kmap)

    def __str__(self):
        return "%s{%s}" % (
            self.__class__.__name__,
            self.kmap)

    def make_node(self, csm):
        csm = as_sparse_variable(csm)
        data = tensor.TensorType(dtype=csm.type.dtype,
                                 broadcastable=(False,)).make_variable()
        return gof.Apply(self, [csm],
                [data, tensor.ivector(), tensor.ivector(), tensor.ivector()])

    def perform(self, node, (csm,), out):
        if self.kmap is None:
            out[0][0] = csm.data
        else:
            out[0][0] = csm.data[self.kmap]
        if str(csm.data.dtype) == 'int32':
            out[0][0] = theano._asarray(out[0][0], dtype='int32')
        #backport
        #out[0][0] = csm.data if self.kmap is None else csm.data[self.kmap]
        out[1][0] = theano._asarray(csm.indices, dtype='int32')
        out[2][0] = theano._asarray(csm.indptr, dtype='int32')
        out[3][0] = theano._asarray(csm.shape, dtype='int32')

    def grad(self, (csm,), g):
        assert [gg is None for gg in g[1:]]
        data, indices, indptr, shape = csm_properties(csm)
        return [CSM(csm.format)(g[0], indices, indptr, shape)]
# don't make this a function or it breaks some optimizations below
csm_properties = CSMProperties()


def csm_data(csm):
    return csm_properties(csm)[0]


def csm_indices(csm):
    return csm_properties(csm)[1]


def csm_indptr(csm):
    return csm_properties(csm)[2]


def csm_shape(csm):
    return csm_properties(csm)[3]


class CSM(gof.Op):
    """Construct a CSC or CSR matrix from the internal
    representation.

    The format for the sparse array can be specified
    through the constructor. Also, `kmap` could be
    set through to constructor to specified the parts
    of the parameter `data` the op should use to construct
    the sparse matrix. Fancy indexing with numpy.ndarray
    should be used for this purpose.

    :param data: One dimensionnal tensor representing
                 the data of the sparse to construct.
    :param indices: One dimensionnal tensor of integers
                    representing the indices of the sparse
                    matrix to construct.
    :param indptr: One dimensionnal tensor of integers
                   representing the indice pointer for
                   the sparse matrix to construct.
    :param shape: One dimensionnal tensor of integers
                  representing the shape of the sparse
                  matrix to construct.

    :return: A sparse matrix having the properties
             speficied by the inputs.

    :note:
    - The grad method returns a dense vector, so it provide
      a regular grad.
    """

    # should view the other inputs too, but viewing multiple inputs is not
    view_map = {0: [0]}
    #currently supported by the destroyhandler

    kmap = None
    """Indexing to speficied what part of the data parameter
    should be use to construct the sparse matrix."""

    _hashval = None
    """Pre-computed hash value, defined by __init__"""

    def __init__(self, format, kmap=None):
        if format not in ('csr', 'csc'):
            raise ValueError("format must be one of: 'csr', 'csc'", format)
        self.format = format

        # for efficiency, if remap does nothing, then do not apply it
        if kmap is not None and all(kmap == numpy.arange(numpy.size(kmap))):
            kmap = None

        self.kmap = kmap

        self._hashval = (hash(type(self)) ^ hash(self.format) ^
                         _kmap_hash(self.kmap))

    def __eq__(self, other):
        return (type(other) is CSM and other.format == self.format and
                _kmap_eq(self.kmap, other.kmap))

    def __hash__(self):
        return self._hashval

    def make_node(self, data, indices, indptr, shape):
        data = tensor.as_tensor_variable(data)

        if not isinstance(indices, tensor.TensorVariable):
            indices = theano._asarray(indices, dtype='int32')
        if not isinstance(indptr, tensor.TensorVariable):
            indptr = theano._asarray(indptr, dtype='int32')
        if not isinstance(shape, tensor.TensorVariable):
            shape = theano._asarray(shape, dtype='int32')
        indices = tensor.as_tensor_variable(indices)
        indptr = tensor.as_tensor_variable(indptr)
        shape = tensor.as_tensor_variable(shape)

        if data.type.ndim != 1:
            raise TypeError('data argument must be a vector', data.type,
                            data.type.ndim)
        if indices.type.ndim != 1 or indices.type.dtype not in discrete_dtypes:
            raise TypeError('indices must be vector of integers', indices,
                            indices.type)
        if indptr.type.ndim != 1 or indptr.type.dtype not in discrete_dtypes:
            raise TypeError('indices must be vector of integers', indptr,
                            indptr.type)
        if shape.type.ndim != 1 or shape.type.dtype not in discrete_dtypes:
            raise TypeError('n_rows must be integer type', shape, shape.type)

        return gof.Apply(self,
                         [data, indices, indptr, shape],
                         [SparseType(dtype=data.type.dtype,
                                     format=self.format).make_variable()])

    def perform(self, node, (data, indices, indptr, shape), (out,)):
        # for efficiency, if remap does nothing, then do not apply it
        if self.kmap is not None:
            data = data[self.kmap]

        if len(shape) != 2:
            raise ValueError('Shape should be an array of length 2')
        if (data.shape != indices.shape and numpy.size(data) !=
            numpy.size(self.kmap)):
            errmsg = ('Data (shape ' + repr(data.shape) +
                      ' must have the same number of elements ' +
                      'as indices (shape' + repr(indices.shape) +
                      ') or elements as kmap (' +
                      repr(numpy.size(self.kmap)) + ')')
            raise ValueError(errmsg)
        if self.format == 'csc':
            out[0] = scipy.sparse.csc_matrix((data, indices.copy(),
                                              indptr.copy()),
                                             numpy.asarray(shape), copy=False)
        else:
            assert self.format == 'csr'
            out[0] = scipy.sparse.csr_matrix((data, indices.copy(),
                                              indptr.copy()), shape.copy(),
                                             copy=False)

    def grad(self, (x_data, x_indices, x_indptr, x_shape), (g_out,)):
        g_data, g_indices, g_indptr, g_shape = csm_properties(g_out)
        #unpack the data vector and wrap it as a 1d TensorType
        g_data = csm_grad(self.kmap)(x_data, x_indices, x_indptr, x_shape,
            g_data, g_indices, g_indptr, g_shape)
        return [g_data, None, None, None]

    def infer_shape(self, node, shapes):
        if self.kmap is None:
            # node.inputs[3] is of lenght as we only support sparse matrix.
            return [(node.inputs[3][0], node.inputs[3][1])]
        else:
            return node.fgraph.shape_feature.default_infer_shape(node, shapes)


CSC = CSM('csc')
CSR = CSM('csr')


class CSMGrad(gof.op.Op):
    # Note
    # This Op computes the gradient of the CSM Op. CSM creates a matrix from
    # data, indices, and indptr vectors; it's gradient is the gradient of
    # the data vector only. There are two complexities to calculate this
    # gradient:
    # 1. The gradient may be sparser than the input matrix defined by (data,
    # indices, indptr). In this case, the data vector of the gradient will have
    # less elements than the data vector of the input because sparse formats
    # remove 0s. Since we are only returning the gradient of the data vector,
    # the relevant 0s need to be added back.
    # 2. The elements in the sparse dimension are not guaranteed to be sorted.
    # Therefore, the input data vector may have a different order than the
    # gradient data vector.

    def __init__(self, kmap=None):
        self.kmap = kmap
        if self.kmap is None:
            self.view_map = {0: [1]}

    def __eq__(self, other):
        return type(self) == type(other) and _kmap_eq(self.kmap, other.kmap)

    def __hash__(self):
        return 82345 ^ hash(type(self)) ^ _kmap_hash(self.kmap)

    def __str__(self):
        return "%s{%s}" % (
            self.__class__.__name__,
            self.kmap)

    def make_node(self, x_data, x_indices, x_indptr, x_shape,
                g_data, g_indices, g_indptr, g_shape):
        gout_data = g_data.type()
        return gof.Apply(self, [x_data, x_indices, x_indptr, x_shape,
            g_data, g_indices, g_indptr, g_shape], [gout_data])

    def perform(self, node, (x_data, x_indices, x_indptr, x_shape,
                g_data, g_indices, g_indptr, g_shape), (g_out,)):
        if len(x_indptr) - 1 == x_shape[0]:
            sp_dim = x_shape[1]
        else:
            sp_dim = x_shape[0]

        g_row = numpy.zeros(sp_dim, dtype=g_data.dtype)
        gout_data = numpy.zeros_like(x_data)
        for i in range(len(x_indptr) - 1):
            for j_ptr in range(g_indptr[i], g_indptr[i + 1]):
                g_row[g_indices[j_ptr]] += g_data[j_ptr]

            for j_ptr in range(x_indptr[i], x_indptr[i + 1]):
                gout_data[j_ptr] = g_row[x_indices[j_ptr]]

            for j_ptr in range(g_indptr[i], g_indptr[i + 1]):
                g_row[g_indices[j_ptr]] = 0

        if self.kmap is None:
            g_out[0] = gout_data
        else:
            grad = numpy.zeros_like(x_data)
            grad[self.kmap] = gout_data
            g_out[0] = grad

    def infer_shape(self, node, shapes):
        if self.kmap is None:
            return [shapes[1]]
        else:
            return [shapes[0]]

csm_grad = CSMGrad


class CSMGradC(gof.Op):
    def __eq__(self, other):
        return (type(self) == type(other))

    def __hash__(self):
        return hash(type(self))

    def __str__(self):
        return self.__class__.__name__

    def make_node(self, a_val, a_ind, a_ptr, a_dim,
                  b_val, b_ind, b_ptr, b_dim):
        return gof.Apply(self, [a_val, a_ind, a_ptr, a_dim,
             b_val, b_ind, b_ptr, b_dim], [b_val.type()])

    def c_code(self, node, name, (a_val, a_ind, a_ptr, a_dim,
                        b_val, b_ind, b_ptr, b_dim), (z,), sub):
        # retrieve dtype number
        typenum_z = node.outputs[0].type.dtype_specs()[-1]
        if node.inputs[0].type.dtype in ('complex64', 'complex128'):
            raise NotImplementedError('Complex types are not supported for a_val')
        if node.inputs[3].type.dtype in ('complex64', 'complex128'):
            raise NotImplementedError('Complex types are not supported for b_val')

        return """
        if (%(a_val)s->nd != 1) {PyErr_SetString(PyExc_NotImplementedError, "rank(a_val) != 1"); %(fail)s;}
        if (%(a_ind)s->nd != 1) {PyErr_SetString(PyExc_NotImplementedError, "rank(a_ind) != 1"); %(fail)s;}
        if (%(a_ptr)s->nd != 1) {PyErr_SetString(PyExc_NotImplementedError, "rank(a_ptr) != 1"); %(fail)s;}
        if (%(b_val)s->nd != 1) {PyErr_SetString(PyExc_NotImplementedError, "rank(b_val) != 1"); %(fail)s;}
        if (%(b_ind)s->nd != 1) {PyErr_SetString(PyExc_NotImplementedError, "rank(b_ind) != 1"); %(fail)s;}
        if (%(b_ptr)s->nd != 1) {PyErr_SetString(PyExc_NotImplementedError, "rank(b_ptr) != 1"); %(fail)s;}

        if (%(a_ind)s->descr->type_num != PyArray_INT32) {
        PyErr_SetString(PyExc_NotImplementedError, "a_ind dtype not INT32"); %(fail)s;}

        if (%(a_ptr)s->descr->type_num != PyArray_INT32)
        {PyErr_SetString(PyExc_NotImplementedError, "a_ptr dtype not INT32"); %(fail)s;}

        if (%(b_ind)s->descr->type_num != PyArray_INT32) {
        PyErr_SetString(PyExc_NotImplementedError, "b_ind dtype not INT32"); %(fail)s;}

        if (%(b_ptr)s->descr->type_num != PyArray_INT32)
        {PyErr_SetString(PyExc_NotImplementedError, "b_ptr dtype not INT32"); %(fail)s;}

        if (%(a_val)s->dimensions[0] != %(a_ind)s->dimensions[0])
        {PyErr_SetString(PyExc_NotImplementedError, "a_val and a_ind have different lengths"); %(fail)s;}

        if (%(b_val)s->dimensions[0] != %(b_ind)s->dimensions[0])
        {PyErr_SetString(PyExc_NotImplementedError, "b_val and b_ind have different lengths"); %(fail)s;}

        if (%(a_ptr)s->dimensions[0] != %(b_ptr)s->dimensions[0])
        {PyErr_SetString(PyExc_NotImplementedError, "a_ptr and b_ptr have different lengths"); %(fail)s;}

        if ((!%(z)s) || (%(z)s->dimensions[0] != %(a_val)s->dimensions[0]))
        {
            {Py_XDECREF(%(z)s);}
            npy_intp dims[] = {0};
            dims[0] = %(a_val)s->dimensions[0];
            %(z)s = (PyArrayObject*) PyArray_SimpleNew(1, dims, %(typenum_z)s);
        }

        {
            // sparse array has size MxK, dense KxN, output MxN
            npy_intp M = %(a_ptr)s->dimensions[0] - 1;
            npy_intp a_dim_0 = ((npy_int32 *)%(a_dim)s->data)[0];
            npy_intp a_dim_1 = ((npy_int32 *)%(a_dim)s->data)[1];

            npy_intp sp_dim = (M == a_dim_0)?a_dim_1:a_dim_0;

            // strides tell you how many bytes to skip to go to next column/row entry
            npy_intp Sz = %(z)s->strides[0] / %(z)s->descr->elsize;
            npy_intp Sa_val = %(a_val)s->strides[0] / %(a_val)s->descr->elsize;
            npy_intp Sa_ind = %(a_ind)s->strides[0] / %(a_ind)s->descr->elsize;
            npy_intp Sa_ptr = %(a_ptr)s->strides[0] / %(a_ptr)s->descr->elsize;
            npy_intp Sb_val = %(b_val)s->strides[0] / %(b_val)s->descr->elsize;
            npy_intp Sb_ind = %(b_ind)s->strides[0] / %(b_ind)s->descr->elsize;
            npy_intp Sb_ptr = %(b_ptr)s->strides[0] / %(b_ptr)s->descr->elsize;

            // pointers to access actual data in the arrays passed as params.
            dtype_%(z)s* __restrict__ Dz = (dtype_%(z)s*)%(z)s->data;
            const dtype_%(a_val)s* __restrict__ Da_val = (dtype_%(a_val)s*)%(a_val)s->data;
            const npy_int32 * __restrict__ Da_ind = (npy_int32*)%(a_ind)s->data;
            const npy_int32 * __restrict__ Da_ptr = (npy_int32*)%(a_ptr)s->data;
            const dtype_%(b_val)s* __restrict__ Db_val = (dtype_%(b_val)s*)%(b_val)s->data;
            const npy_int32 * __restrict__ Db_ind = (npy_int32*)%(b_ind)s->data;
            const npy_int32 * __restrict__ Db_ptr = (npy_int32*)%(b_ptr)s->data;

            npy_intp nnz = %(a_ind)s->dimensions[0];

            dtype_%(b_val)s b_row[sp_dim];

            //clear the output array
            for (npy_int64 i = 0; i < nnz; ++i)
            {
                Dz[i*Sz] = 0;
            }
            memset(b_row, 0, sp_dim*sizeof(dtype_%(b_val)s));

            // loop over inner dimension
            for (npy_int64 m = 0; m < M; ++m)
            {
                for (npy_int32 j_ptr = Db_ptr[m * Sb_ptr];
                    j_ptr < Db_ptr[(m + 1) * Sb_ptr]; j_ptr++) {
                    b_row[Db_ind[j_ptr * Sb_ind]] += Db_val[j_ptr*Sb_val];
                }

                for (npy_int32 j_ptr = Da_ptr[m * Sa_ptr];
                    j_ptr < Da_ptr[(m + 1) * Sa_ptr]; j_ptr++) {
                    Dz[j_ptr*Sz] = b_row[Da_ind[j_ptr * Sa_ind]];
                }

                for (npy_int32 j_ptr = Db_ptr[m * Sb_ptr];
                    j_ptr < Db_ptr[(m + 1) * Sb_ptr]; j_ptr++) {
                    b_row[Db_ind[j_ptr * Sb_ind]] = 0;
                }
            }
        }

        """ % dict(locals(), **sub)

    def c_code_cache_version(self):
        return (3,)
csm_grad_c = CSMGradC()


class Cast(gof.op.Op):
    """Cast sparse variable to the desired dtype.

    :param x: Sparse matrix.

    :return: Same as `x` but having `out_type` as dtype.

    :note:
    - The grad implemented is regular, i.e. not structured.
    """

    def __init__(self, out_type):
        self.out_type = out_type

    def __eq__(self, other):
        return (type(self) == type(other)) and self.out_type == other.out_type

    def __hash__(self):
        return hash(type(self)) ^ hash(self.out_type)

    def make_node(self, x):
        x = as_sparse_variable(x)
        return gof.Apply(
            self, [x],
            [SparseType(dtype=self.out_type, format=x.format).make_variable()])

    def perform(self, node, (x, ), (out, )):
        assert _is_sparse(x)
        out[0] = x.astype(self.out_type)

    def grad(self, inputs, outputs_gradients):
        if inputs[0].dtype in tensor.continuous_dtypes:
            gz = outputs_gradients[0]
            return [Cast(inputs[0].dtype)(gz)]
        else:
            return [None]

    def infer_shape(self, node, ins_shapes):
        return ins_shapes

    def __str__(self):
        return "%s(%s)" % (self.__class__.__name__, self.out_type)

bcast = Cast('int8')
wcast = Cast('int16')
icast = Cast('int32')
lcast = Cast('int64')
fcast = Cast('float32')
dcast = Cast('float64')
ccast = Cast('complex64')
zcast = Cast('complex128')


#
# Conversion
#


class DenseFromSparse(gof.op.Op):
    """Convert a sparse matrix to a dense one.

    :param x: A sparse matrix.

    :return: A dense matrix, the same as `x`.

    :note:
    - The grad implementation can be controlled
      through the constructor via the `structured`
      parameter. `True` will provide a structured
      grad while `False` will provide a regular
      grad. By default, the grad is structured.
    """

    def __init__(self, structured=True):
        self.sparse_grad = structured

    def __eq__(self, other):
        return (type(self) == type(other)) and \
            (self.sparse_grad == other.sparse_grad)

    def __hash__(self):
        return hash(type(self)) ^ hash(self.sparse_grad)

    def __str__(self):
        return "%s{structured_grad=%s}" % (
            self.__class__.__name__,
            self.sparse_grad)

    def make_node(self, x):
        x = as_sparse_variable(x)
        return gof.Apply(self,
                         [x],
                         [tensor.TensorType(dtype=x.type.dtype,
                                            broadcastable=(False, False)
                                           ).make_variable()])

    def perform(self, node, (x, ), (out, )):
        if _is_dense(x):
            print >> sys.stderr, (
                "WARNING: You just called DenseFromSparse on a dense matrix."
            )
            out[0] = x
        else:
            out[0] = x.toarray()
        assert _is_dense(out[0])

    def grad(self, (x, ), (gz, )):
        if self.sparse_grad:
            return [sp_ones_like(x) * gz]
        else:
            return [SparseFromDense(x.type.format)(gz)]

    def infer_shape(self, node, shapes):
        return [shapes[0]]

dense_from_sparse = DenseFromSparse()


class SparseFromDense(gof.op.Op):
    """Convert a dense matrix to a sparse matrix.

    To convert in CSR format, use `csr_from_dense`
    and to convert in CSC format, use `csc_from_dense`.

    :param x: A dense matrix.

    :return: The same as `x` in a sparse matrix
             format.

    :note:
    - The grad implementation is regular, i.e.
      not structured.
    - The output sparse format can also be controlled
      via the `format` parameter in the constructor.
    """

    def __init__(self, format):
        self.format = format

    def __eq__(self, other):
        return type(self) == type(other) and self.format == other.format

    def __hash__(self):
        return 982374 ^ hash(self.format) ^ hash(DenseFromSparse)

    def __str__(self):
        return "%s{%s}" % (
            self.__class__.__name__,
            self.format)

    def make_node(self, x):
        x = tensor.as_tensor_variable(x)
        if x.ndim > 2:
            raise TypeError(
                    "Theano does not have sparse tensor types with more "
                    "than 2 dimensions, but %s.ndim = %i" % (x, x.ndim))
        elif x.ndim == 1:
            x = x.dimshuffle('x', 0)
        elif x.ndim == 0:
            x = x.dimshuffle('x', 'x')
        else:
            assert x.ndim == 2

        return gof.Apply(self,
                         [x],
                         [SparseType(dtype=x.type.dtype,
                                     format=self.format
                                    ).make_variable()])

    def perform(self, node, (x, ), (out, )):
        out[0] = SparseType.format_cls[self.format](x)

    def grad(self, (x, ), (gz, )):
        gx = dense_from_sparse(gz)
        gx = tensor.patternbroadcast(gx, x.broadcastable)
        return gx,

    def infer_shape(self, node, shapes):
        return [shapes[0]]

csr_from_dense = SparseFromDense('csr')
csc_from_dense = SparseFromDense('csc')


# Indexing
class GetItem2d(gof.op.Op):
    """Implement a subtensor of sparse variable and that return a
    sparse matrix.

    If you want to take only one element of a sparse matrix see
    `GetItemScalar` that return a tensor scalar.

    .. note::

        Subtensor selection always returns a matrix, so indexing
        with [a:b, c:d] is forced.  If one index is a scalar. For
        instance, x[a:b, c] and x[a, b:c], generate an error. Use
        instead x[a:b, c:c+1] and x[a:a+1, b:c].

    The above indexing methods are not supported because the return value
    would be a sparse matrix rather than a sparse vector, which is a
    deviation from numpy indexing rule.  This decision is made largely
    for keeping the consistency between numpy and theano. Subjected
    to modification when sparse vector is supported.

    :param x: Sparse matrix.
    :param index: Tuple of slice object.

    :return: The slice corresponding in `x`.

    :note:
    - The grad is not implemented for this op.
    """

    def __eq__(self, other):
        return (type(self) == type(other))

    def __hash__(self):
        return hash(type(self))

# Fred:Too complicated for now. If you need it, look at
# the Subtensor.infer_shape.
#    def infer_shape(self, node, i0_shapes):
#        return i0_shapes

    def make_node(self, x, index):
        x = as_sparse_variable(x)
        assert len(index) in [1, 2]

        input_op = [x]
        generic_None = theano.gof.Constant(theano.gof.generic, None)

        for ind in index:
            if isinstance(ind, slice):
                # in case of slice is written in theano variable
                start = ind.start
                stop = ind.stop
                if ind.step is not None:
                    raise ValueError((
                        "Using a slice with non-default step when "
                        "indexing into a sparse matrix is not supported. "),
                        ind, ind.step)

                # If start or stop are None, make them a Generic constant
                # Else, they should be converted to Tensor Variables of
                # dimension 1 and int/uint dtype.
                if start is None:
                    start = generic_None
                else:
                    if not isinstance(start, gof.Variable):
                        start = tensor.as_tensor_variable(start)
                    if not (start.ndim == 0 and start.dtype in
                            tensor.discrete_dtypes):
                        raise ValueError((
                            "Impossible to index into a sparse matrix with "
                            "slice where start=%s" % start),
                            start.ndim, start.dtype)

                if stop is None:
                    stop = generic_None
                else:
                    if not isinstance(stop, gof.Variable):
                        stop = tensor.as_tensor_variable(stop)
                    if not (stop.ndim == 0 and stop.dtype in
                            tensor.discrete_dtypes):
                        raise ValueError((
                            "Impossible to index into a sparse matrix with "
                            "slice where stop=%s" % stop),
                            stop.ndim, stop.dtype)

            elif ((isinstance(ind, gof.Variable) and
                        getattr(ind, 'ndim', -1) == 0)
                        or numpy.isscalar(ind)):
                raise NotImplementedError(
                    'Theano has no sparse vector' +
                    'Use X[a:b, c:d], X[a:b, c:c+1] or X[a:b] instead.')
            else:
                raise ValueError((
                    'Advanced indexing is not implemented for sparse '
                    'matrices. Argument not supported: %s' % ind))
            input_op += [start, stop]
        if len(index) == 1:
            input_op += [generic_None, generic_None]

        return gof.Apply(self, input_op, [x.type()])

    def perform(self, node, (x, start1, stop1, start2, stop2), (out, )):
        assert _is_sparse(x)
        out[0] = x[start1:stop1, start2:stop2]

    def __str__(self):
        return self.__class__.__name__

get_item_2d = GetItem2d()


class GetItemScalar(gof.op.Op):
    """Implement a subtensor of a sparse variable that take
    two scalar as index and return a scalar.

    If you want to take a slice of a sparse matrix see
    `GetItem2d` that return a sparse matrix.

    :param x: Sparse matrix.
    :param index: Tuple of scalar..

    :return: The item corresponding in `x`.

    :note:
    - The grad is not implemented for this op.
    """

    def __eq__(self, other):
        return (type(self) == type(other))

    def __hash__(self):
        return hash(type(self))

    def infer_shape(self, node, shapes):
        return [()]

    def make_node(self, x, index):
        x = as_sparse_variable(x)
        assert len(index) == 2

        input_op = [x]

        for ind in index:

            if isinstance(ind, slice):
                raise Exception("GetItemScalar called with a slice as index!")

            #in case of indexing using int instead of theano variable
            elif isinstance(ind, int):
                ind = theano.tensor.constant(ind)
                input_op += [ind]

            # in case of indexing using theano variable
            elif ind.ndim == 0:
                input_op += [ind]
            else:
                raise NotImplemented()

        return gof.Apply(self, input_op, [tensor.scalar(dtype=x.dtype)])

    def perform(self, node, (x, ind1, ind2), (out, )):
        assert _is_sparse(x)
        out[0] = theano._asarray(x[ind1, ind2], x.dtype)

    def __str__(self):
        return self.__class__.__name__

get_item_scalar = GetItemScalar()


# Linear Algebra
class Transpose(gof.op.Op):
    """Return the transpose of the sparse matrix.

    :param x: Sparse matrix.

    :return: `x` transposed.

    :note:
    - The returned matrix will not be in the same format. `csc`
      matrix will be changed in `csr` matrix and `csr` matrix in
      `csc` matrix.
    - The grad is regular, i.e. not structured.
    """

    format_map = {'csr': 'csc',
                  'csc': 'csr'}

    def __eq__(self, other):
        return (type(self) == type(other))

    def __hash__(self):
        return hash(type(self))

    def __str__(self):
        return "Sparse" + self.__class__.__name__

    def make_node(self, x):
        x = as_sparse_variable(x)
        return gof.Apply(self,
                         [x],
                         [SparseType(dtype=x.type.dtype,
                                     format=self.format_map[x.type.format]
                                 ).make_variable()])

    def perform(self, node, (x, ), (out, )):
        assert _is_sparse(x)
        out[0] = x.transpose()

    def grad(self, (x,), (gz,)):
        assert _is_sparse_variable(x) and _is_sparse_variable(gz)
        return transpose(gz),

    def infer_shape(self, node, shapes):
        return [shapes[0][::-1]]
transpose = Transpose()


class Neg(gof.op.Op):
    """Return the negation of the sparse matrix.

    :param x: Sparse matrix.

    :return: -`x`.

    :note:
    - The grad is regular, i.e. not structured.
    """

    def __eq__(self, other):
        return (type(self) == type(other))

    def __hash__(self):
        return hash(type(self))

    def __str__(self):
        return "Sparse" + self.__class__.__name__

    def make_node(self, x):
        x = as_sparse_variable(x)
        return gof.Apply(self, [x], [x.type()])

    def perform(self, node, (x, ), (out, )):
        assert _is_sparse(x)
        out[0] = -x

    def grad(self, (x,), (gz,)):
        assert _is_sparse_variable(x) and _is_sparse_variable(gz)
        return -gz,

    def infer_shape(self, node, shapes):
        return [shapes[0]]
neg = Neg()


class ColScaleCSC(gof.op.Op):
    # Scale each columns of a sparse matrix by the corresponding
    # element of a dense vector

    # :param x: A sparse matrix.
    # :param s: A dense vector with length equal to the number
    #           of columns of `x`.

    # :return: A sparse matrix in the same format as `x` which
    #          each column had been multiply by the corresponding
    #          element of `s`.

    # :note:
    # - The grad implemented is structured.

    def __eq__(self, other):
        return type(self) == type(other)

    def __hash__(self):
        return hash(type(self))

    def make_node(self, x, s):
        if x.format != 'csc':
            raise ValueError('x was not a csc matrix')
        return gof.Apply(self, [x, s], [x.type()])

    def perform(self, node, (x, s), (z,)):
        M, N = x.shape
        assert x.format == 'csc'
        assert s.shape == (N, )

        y = x.copy()

        for j in xrange(0, N):
            y.data[y.indptr[j]: y.indptr[j + 1]] *= s[j]

        z[0] = y

    def grad(self, (x, s), (gz,)):
        return [col_scale(gz, s), sp_sum(x * gz, axis=0)]

    def infer_shape(self, node, ins_shapes):
        return [ins_shapes[0]]

    def __str__(self):
        return self.__class__.__name__


class RowScaleCSC(gof.op.Op):
    # Scale each row of a sparse matrix by the corresponding element of
    # a dense vector

    # :param x: A sparse matrix.
    # :param s: A dense vector with length equal to the number
    #           of rows of `x`.

    # :return: A sparse matrix in the same format as `x` which
    #          each row had been multiply by the corresponding
    #          element of `s`.

    # :note:
    # - The grad implemented is structured.

    def __eq__(self, other):
        return type(self) == type(other)

    def __hash__(self):
        return hash(type(self))

    def make_node(self, x, s):
        return gof.Apply(self, [x, s], [x.type()])

    def perform(self, node, (x, s), (z,)):
        M, N = x.shape
        assert x.format == 'csc'
        assert s.shape == (M, )

        indices = x.indices
        indptr = x.indptr

        y_data = x.data.copy()

        for j in xrange(0, N):
            for i_idx in xrange(indptr[j], indptr[j + 1]):
                y_data[i_idx] *= s[indices[i_idx]]

        z[0] = scipy.sparse.csc_matrix((y_data, indices, indptr), (M, N))

    def grad(self, (x, s), (gz,)):
        return [row_scale(gz, s), sp_sum(x * gz, axis=1)]

    def infer_shape(self, node, ins_shapes):
        return [ins_shapes[0]]

    def __str__(self):
        return self.__class__.__name__


def col_scale(x, s):
    """Scale each columns of a sparse matrix by the corresponding
    element of a dense vector

    :param x: A sparse matrix.
    :param s: A dense vector with length equal to the number
              of columns of `x`.

    :return: A sparse matrix in the same format as `x` which
             each column had been multiply by the corresponding
             element of `s`.

    :note:
    - The grad implemented is structured.
    """

    if x.format == 'csc':
        return ColScaleCSC()(x, s)
    elif x.format == 'csr':
        return RowScaleCSC()(x.T, s).T
    else:
        raise NotImplementedError()


def row_scale(x, s):
    """Scale each row of a sparse matrix by the corresponding element of
    a dense vector

    :param x: A sparse matrix.
    :param s: A dense vector with length equal to the number
              of rows of `x`.

    :return: A sparse matrix in the same format as `x` which
             each row had been multiply by the corresponding
             element of `s`.

    :note:
    - The grad implemented is structured.
    """
    return col_scale(x.T, s).T


class SpSum(gof.op.Op):
    """Calculate the sum of a sparse matrix along a specify
    axis.

    It operates a reduction along the axis specified. When
    `axis` is `None`, it is apply along all axis.

    :param x: Sparse matrix.
    :param axis: Axis along the sum is apply. Integers or `None`.
    :param sparse_grad: `True` to have a structured grad. Boolean.

    :return: The sum of `x` in a dense format.

    :note:
    - The grad implementation is controlled with the `sparse_grad`
      parameter. `True` will provide a structured grad and `False`
      will provide a regular grad. For both choice, the grad
      return a sparse matrix having the same format as `x`.
    - This op does not return a sparse matrix, but a dense tensor
      matrix.
    """

    def __init__(self, axis=None, sparse_grad=True):
        super(SpSum, self).__init__()
        self.axis = axis
        self.structured = sparse_grad
        if self.axis not in (None, 0, 1):
            raise ValueError('Illegal value for self.axis.')

    def __eq__(self, other):
        # WARNING: judgement call...
        # We are not using the structured in the comparison or hashing
        # because it doesn't change the perform method therefore, we
        # *do* want Sums with different structured values to be merged
        # by the merge optimization and this requires them to compare equal.
        return type(self) == type(other) and self.axis == other.axis

    def __hash__(self):
        # WARNING: judgement call...
        # We are not using the structured in the comparison or hashing
        # because it doesn't change the perform method therefore, we
        # *do* want Sums with different structured values to be merged
        # by the merge optimization and this requires them to compare equal.
        return 76324 ^ hash(type(self)) ^ hash(self.axis)

    def make_node(self, x):
        x = as_sparse_variable(x)
        b = ()
        if self.axis is not None:
            b = (False,)

        z = tensor.TensorType(broadcastable=b, dtype=x.dtype)()
        return gof.Apply(self, [x], [z])

    def perform(self, node, (x,), (z,)):
        if self.axis == None:
            z[0] = numpy.asarray(x.sum())
        else:
            z[0] = numpy.asarray(x.sum(self.axis)).ravel()

    def grad(self, (x,), (gz,)):
        if x.dtype not in continuous_dtypes:
            return [None]

        if self.structured:
            if self.axis is None:
                r = gz * theano.sparse.sp_ones_like(x)
            elif self.axis == 0:
                r = col_scale(theano.sparse.sp_ones_like(x), gz)
            elif self.axis == 1:
                r = row_scale(theano.sparse.sp_ones_like(x), gz)
            else:
                raise ValueError('Illegal value for self.axis.')
        else:
            o_format = x.format
            x = dense_from_sparse(x)
            if _is_sparse_variable(gz):
                gz = dense_from_sparse(gz)
            if self.axis is None:
                r = tensor.second(x, gz)
            else:
                ones = tensor.ones_like(x)
                if self.axis == 0:
                    r = tensor.addbroadcast(gz.dimshuffle('x', 0), 0) * ones
                elif self.axis == 1:
                    r = tensor.addbroadcast(gz.dimshuffle(0, 'x'), 1) * ones
                else:
                    raise ValueError('Illegal value for self.axis.')
            r = SparseFromDense(o_format)(r)
        return [r]

    def infer_shape(self, node, shapes):
        r = None
        if self.axis is None:
            r = [()]
        elif self.axis == 0:
            r = [(shapes[0][1],)]
        else:
            r = [(shapes[0][0],)]
        return r

    def __str__(self):
        return self.__class__.__name__ + "{axis=%s}" % str(self.axis)


def sp_sum(x, axis=None, sparse_grad=False):
    return SpSum(axis, sparse_grad)(x)


class Diag(gof.op.Op):
    """Extract the diagonal of a square sparse matrix as a dense
    vector.

    :param x: A square sparse matrix in csc format.

    :return: A dense vector representing the diagonal elements.

    :note:
    - The grad implemented is regular, i.e. not structured, since
      the output is a dense vector.
    """

    def __eq__(self, other):
        return (type(self) == type(other))

    def __hash__(self):
        return hash(type(self))

    def make_node(self, x):
        return gof.Apply(self, [x], [tensor.tensor(broadcastable=(False,),
                                                   dtype=x.dtype)])

    def perform(self, node, (x,), (z,)):
        N, M = x.shape
        if N != M:
            raise ValueError('Diag only apply on square matrix')
        z[0] = x.diagonal()

    def grad(self, (x,), (gz,)):
        return [square_diagonal(gz)]

    def infer_shape(self, nodes, shapes):
        return [(tensor.minimum(*shapes[0]), )]

    def __str__(self):
        return self.__class__.__name__
diag = Diag()


class SquareDiagonal(gof.op.Op):
    """Return a square sparse (csc) matrix whose diagonal
    is given by the dense vector argument.

    :param x: Dense vector for the diagonal.

    :return: A sparse matrix having `x` as diagonal.

    :note:
    - The grad implemented is regular, i.e. not structured.
    """

    def __eq__(self, other):
        return type(self) == type(other)

    def __hash__(self):
        return hash(type(self))

    def make_node(self, diag):
        diag = tensor.as_tensor_variable(diag)
        if diag.type.ndim != 1:
            raise TypeError('data argument must be a vector', diag.type)

        return gof.Apply(self, [diag],
                [SparseType(dtype=diag.dtype, format='csc')()])

    def perform(self, node, inputs, (z,)):
        diag, o_shape = inputs[0], inputs[0].shape * 2

        N = len(diag)
        data = diag[:N]
        indices = range(N)
        indptr = range(N + 1)
        tup = (data, indices, indptr)

        z[0] = scipy.sparse.csc_matrix(tup, copy=True)

    def grad(self, inputs, (gz,)):
        return [diag(gz)]

    def infer_shape(self, nodes, shapes):
        return [(shapes[0][0], shapes[0][0])]

    def __str__(self):
        return self.__class__.__name__
square_diagonal = SquareDiagonal()


class EnsureSortedIndices(gof.op.Op):
    """Resort indices of a sparse matrix.

    CSR column indices are not necessarily sorted. Likewise
    for CSC row indices. Use `ensure_sorted_indices` when sorted
    indices are required (e.g. when passing data to other
    libraries).

    :param x: A sparse matrix.

    :return: The same as `x` with indices sorted.

    :note:
    - The grad implemented is regular, i.e. not structured.
    """

    def __init__(self, inplace):
        self.inplace = inplace
        if self.inplace:
            self.view_map = {0: [0]}

    def __eq__(self, other):
        return type(self) == type(other)

    def __hash__(self):
        return hash(type(self))

    def make_node(self, x):
        return gof.Apply(self, [x], [x.type()])

    def perform(self, node, (x, ), (z, )):
        if self.inplace:
            z[0] = x.sort_indices()
        else:
            z[0] = x.sorted_indices()

    def grad(self, inputs, output_grad):
        return [output_grad[0]]

    def infer_shape(self, node, i0_shapes):
        return i0_shapes

    def __str__(self):
        if self.inplace:
            return self.__class__.__name__ + "{inplace}"
        else:
            return self.__class__.__name__ + "{no_inplace}"
ensure_sorted_indices = EnsureSortedIndices(inplace=False)


def clean(x):
    """Remove explicit zeros from a sparse matrix, and
    resort indices.

    CSR column indices are not necessarily sorted. Likewise
    for CSC row indices. Use `clean` when sorted
    indices are required (e.g. when passing data to other
    libraries) and to ensure there is no zeros in the data.

    :param x: A sparse matrix.

    :return: The same as `x` with indices sorted and zeros
             removed.

    :note:
    - The grad implemented is regular, i.e. not structured.
    """
    return ensure_sorted_indices(remove0(x))


class AddSS(gof.op.Op):
    """Add tw sparse matrix.

    :param x: A sparse matrix.
    :param y: A sparse matrix

    :return: `x`+`y`

    :note:
    - The grad implemented is regular, i.e. not structured.
    """

    def __eq__(self, other):
        return (type(self) == type(other))

    def __hash__(self):
        return hash(type(self))

    def __str__(self):
        return self.__class__.__name__

    def make_node(self, x, y):
        x, y = map(as_sparse_variable, [x, y])
        if x.type.dtype != y.type.dtype:
            raise NotImplementedError()
        if x.type.format != y.type.format:
            raise NotImplementedError()
        return gof.Apply(self,
                         [x, y],
                         [SparseType(dtype=x.type.dtype,
                                     format=x.type.format
                                    ).make_variable()])

    def perform(self, node, (x, y), (out, )):
        assert _is_sparse(x) and _is_sparse(y)
        assert x.shape == y.shape
        out[0] = x + y

    def grad(self, (x, y), (gz,)):
        assert _is_sparse_variable(x) and _is_sparse_variable(y)
        assert _is_sparse_variable(gz)
        return gz, gz

    def infer_shape(self, node, shapes):
        return [shapes[0]]

add_s_s = AddSS()


class AddSSData(gof.op.Op):
    """Add two sparse matrices assuming they have the same sparsity
    pattern.

    :param x: Sparse matrix.
    :param y: Sparse matrix.

    :return: The sum of the two sparse matrix element wise.

    :note:
    - `x` and `y` are assumed to have the same sparsity pattern.
    - The grad implemented is structured.
    """

    def __eq__(self, other):
        return (type(self) == type(other))

    def __hash__(self):
        return hash(type(self))

    def make_node(self, x, y):
        x, y = map(as_sparse_variable, [x, y])
        if x.type.dtype != y.type.dtype:
            raise NotImplementedError()
        if x.type.format != y.type.format:
            raise NotImplementedError()
        return gof.Apply(self,
                         [x, y],
                         [SparseType(dtype=x.type.dtype,
                                 format=x.type.format).make_variable()])

    def perform(self, node, (x, y), (out, )):
        assert _is_sparse(x) and _is_sparse(y)
        assert x.shape == y.shape
        assert x.data.shape == y.data.shape
        out[0] = x.copy()
        out[0].data += y.data

    def grad(self, inputs, (gz, )):
        is_continuous = [(i.dtype in continuous_dtypes)
                         for i in inputs]
        derivative = {True: gz, False: None}
        return [derivative[b] for b in is_continuous]

    def infer_shape(self, node, ins_shapes):
        return [ins_shapes[0]]

    def __str__(self):
        return self.__class__.__name__
add_s_s_data = AddSSData()


class AddSD(gof.op.Op):
    """Add a sparse and a dense matrix.

    :param x: A sparse matrix.
    :param y: A dense matrix

    :return: `x`+`y`

    :note:
    - The grad implemented is structured on `x`.
    """

    def __eq__(self, other):
        return (type(self) == type(other))

    def __hash__(self):
        return hash(type(self))

    def __str__(self):
        return self.__class__.__name__

    def make_node(self, x, y):
        x, y = as_sparse_variable(x), tensor.as_tensor_variable(y)
        if x.type.dtype != y.type.dtype:
            raise NotImplementedError()
        # The magic number two here arises because L{scipy.sparse}
        # objects must be matrices (have dimension 2)
        assert y.type.ndim == 2
        return gof.Apply(self,
                         [x, y],
                         [tensor.TensorType(dtype=y.type.dtype,
                                            broadcastable=y.type.broadcastable
                                           ).make_variable()])

    def perform(self, node, (x, y), (out, )):
        assert _is_sparse(x) and _is_dense(y)
        # The asarray is needed as in some case, this return a
        # numpy.matrixlib.defmatrix.matrix object and not an ndarray.
        out[0] = theano._asarray(x + y, dtype=node.outputs[0].type.dtype)

    def grad(self, (x, y), (gz,)):
        assert _is_sparse_variable(x) and _is_dense_variable(y)
        assert _is_dense_variable(gz)
        return sp_ones_like(x) * gz, gz

    def infer_shape(self, node, shapes):
        return [shapes[0]]

add_s_d = AddSD()


class StructuredAddSV(gof.op.Op):
    """Structured addition of a sparse matrix and a dense vector.
    The elements of the vector are are only added to the corresponding
    non-zero elements. Therefore, this operation outputs another sparse
    matrix.

    :param x: Sparse matrix.
    :param y: Tensor type vector.

    :return: A sparse matrix containing the addition of the vector to
             the data of the sparse matrix.

    :note: The grad implemented is structured since the op is structured.
    """

    def __eq__(self, other):
        return (type(self) == type(other))

    def __hash__(self):
        return hash(type(self))

    def make_node(self, x, y):
        x = as_sparse_variable(x)
        y = tensor.as_tensor_variable(y)

        assert y.type.ndim == 1

        if x.type.dtype != y.type.dtype:
            raise NotImplementedError()
        return gof.Apply(self,
                         [x, y],
                         [SparseType(dtype=x.type.dtype,
                                 format=x.type.format).make_variable()])

    def perform(self, node, (x, y), (out, )):
        assert _is_sparse(x) and not _is_sparse(y)
        assert x.shape[1] == y.shape[0]
        out[0] = x.__class__(x + (x.toarray() != 0) * y)

    def grad(self, (x, y), (gz,)):
        assert _is_sparse_variable(x) and not _is_sparse_variable(y)
        assert _is_sparse_variable(gz)
        return gz, sp_sum(gz, axis=0, sparse_grad=True)

    def infer_shape(self, node, ins_shapes):
        return [ins_shapes[0]]

    def __str__(self):
        return self.__class__.__name__
structured_add_s_v = StructuredAddSV()


class StructuredAddSVCSR(gof.Op):
    # Structured addition of a sparse matrix and a dense vector.
    # The elements of the vector are are only added to the corresponding
    # non-zero elements. Therefore, this operation outputs another sparse
    # matrix.

    # :param a_data: Sparse matrix data.
    # :param a_indices: Sparse matrix indices.
    # :param a_indptr: Sparse matrix indptr.
    # :param b: Tensor type vector.

    # :return: A sparse matrix containing the addition of the vector to
    #          the data of the sparse matrix.

    # :note:
    # - The a_* are the properties of a sparse matrix in csr
    #   format.
    # - This op is used as an optimization for StructuredAddSV.

    def __eq__(self, other):
        return (type(self) == type(other))

    def __hash__(self):
        return hash(type(self))

    def make_node(self, a_data, a_indices, a_indptr, b):
        b = tensor.as_tensor_variable(b)
        a_data = tensor.as_tensor_variable(a_data)
        a_indices = tensor.as_tensor_variable(a_indices)
        a_indptr = tensor.as_tensor_variable(a_indptr)
        assert a_data.type.ndim == 1
        assert a_indices.type.ndim == 1
        assert a_indptr.type.ndim == 1
        assert b.type.ndim == 1
        return gof.Apply(self, [a_data, a_indices, a_indptr, b],
                               [tensor.tensor(b.dtype, (False,))])

    def c_code_cache_version(self):
        return (1,)

    def c_code(self, node, name, inputs, outputs, sub):
        _data, _indices, _indptr, _b, = inputs
        _zout, = outputs
        if node.inputs[0].type.dtype in ('complex64', 'complex128'):
            raise NotImplementedError('Complex types are not supported for a')
        if node.inputs[3].type.dtype in ('complex64', 'complex128'):
            raise NotImplementedError('Complex types are not supported for b')

        return """
        if (%(_b)s->nd != 1) {
            PyErr_SetString(PyExc_NotImplementedError, "rank(b) != 1");
            %(fail)s;
        }
        if (%(_data)s->nd != 1) {
            PyErr_SetString(PyExc_NotImplementedError, "rank(data) != 1");
            %(fail)s;
        }
        if (%(_indices)s->nd != 1) {
            PyErr_SetString(PyExc_NotImplementedError, "rank(indices) != 1");
            %(fail)s;
        }
        if (%(_indptr)s->nd != 1) {
            PyErr_SetString(PyExc_NotImplementedError, "rank(indptr) != 1");
            %(fail)s;
        }

        if( %(_indices)s->descr->type_num != PyArray_INT32) {
        PyErr_SetString(PyExc_NotImplementedError, "C"); %(fail)s;}

        if( %(_indptr)s->descr->type_num != PyArray_INT32)
        {PyErr_SetString(PyExc_NotImplementedError, "D"); %(fail)s;}

        if (!%(_zout)s)
        {
            %(_zout)s = (PyArrayObject*) PyArray_SimpleNew(1,
                    %(_indices)s->dimensions, %(_b)s->descr->type_num);
        }

        if (%(_zout)s->dimensions[0] != %(_indices)s->dimensions[0])
        {
            PyErr_SetString(PyExc_NotImplementedError,
     "somehow _zout got the wrong size.. and I don't know how to resize it.");
            %(fail)s;
        }

        { //makes it compile even though labels jump over variable definitions.
            const npy_intp nnz = %(_indices)s->dimensions[0];
            //TODO: error checking with this
            const npy_intp N =  %(_indptr)s->dimensions[0]-1;

            const dtype_%(_data)s * const __restrict__ data = (dtype_%(_data)s*)%(_data)s->data;
            const npy_int32 * const __restrict__ indptr = (npy_int32 *)%(_indptr)s->data;
            const npy_int32 * const __restrict__ indices = (npy_int32 *)%(_indices)s->data;

            const dtype_%(_b)s* __restrict__ Db = (dtype_%(_b)s*)%(_b)s->data;

            dtype_%(_zout)s * const __restrict__ zout = (dtype_%(_zout)s*)%(_zout)s->data;

            const npy_intp Sb = %(_b)s->strides[0] / %(_b)s->descr->elsize;

            // loop over columns
            for (npy_int32 j = 0; j < N; ++j)
            {
                // for each non-null value in the sparse column
                for (npy_int32 i_idx = indptr[j]; i_idx < indptr[j+1]; ++i_idx)
                {
                    // extract row index of non-null value
                    npy_int32 i = indices[i_idx];

                    // write resulting gradient to sparse output
                    zout[i_idx] = data[i_idx] + Db[i * Sb];
                }
            }
        }

        """ % dict(locals(), **sub)

    def __str__(self):
        return self.__class__.__name__
structured_add_s_v_csr = StructuredAddSVCSR()


def add(x, y):
    """Add two matrices, at least one of which is sparse.

    This method will provide the right op according
    to the inputs.

    :param x: A matrix variable.
    :param y: A matrix variable.

    :return: `x` + `y`

    :note:
    - At least one of `x` and `y` must be a sparse matrix.
    - The grad will be structured only when one of the variable
      will be a dense matrix.
    """

    if hasattr(x, 'getnnz'):
        x = as_sparse_variable(x)
    if hasattr(y, 'getnnz'):
        y = as_sparse_variable(y)

    x_is_sparse_variable = _is_sparse_variable(x)
    y_is_sparse_variable = _is_sparse_variable(y)

    assert x_is_sparse_variable or y_is_sparse_variable
    if x_is_sparse_variable and y_is_sparse_variable:
        return add_s_s(x, y)
    elif x_is_sparse_variable and not y_is_sparse_variable:
        return add_s_d(x, y)
    elif y_is_sparse_variable and not x_is_sparse_variable:
        return add_s_d(y, x)
    else:
        raise NotImplementedError()


def sub(x, y):
    """Substact two matrices, at least one of which is sparse.

    This method will provide the right op according
    to the inputs.

    :param x: A matrix variable.
    :param y: A matrix variable.

    :return: `x` - `y`

    :note:
    - At least one of `x` and `y` must be a sparse matrix.
    - The grad will be structured only when one of the variable
      will be a dense matrix.
    """

    return x + (-y)


class MulSS(gof.op.Op):
    """Elementwise multiply a sparse and a sparse.

    :param x: A sparse matrix.
    :param y: A sparse matrix.

    :return: `x` * `y`

    :note:
    - At least one of `x` and `y` must be a sparse matrix.
    """

    def __eq__(self, other):
        return (type(self) == type(other))

    def __hash__(self):
        return hash(type(self))

    def __str__(self):
        return self.__class__.__name__

    def make_node(self, x, y):
        x, y = as_sparse_variable(x), as_sparse_variable(y)
        if x.type != y.type:
            raise NotImplementedError()
        return gof.Apply(self, [x, y], [x.type()])

    def perform(self, node, (x, y), (out, )):
        assert _is_sparse(x) and _is_sparse(y)
        assert len(x.shape) == 2
        assert y.shape == x.shape
        # This calls the element-wise multiple
        # x * y calls dot...
        out[0] = x.multiply(y)

    def grad(self, (x, y), (gz,)):
        return y * gz, x * gz

    def infer_shape(self, node, shapes):
        return [shapes[0]]

mul_s_s = MulSS()


class MulSD(gof.op.Op):
    """Elementwise multiply a sparse and a dense matrix.

    :param x: A sparse matrix.
    :param y: A dense matrix.

    :return: `x` * `y`

    :note:
    - The grad is regular, i.e. not structured..
    """

    def __eq__(self, other):
        return (type(self) == type(other))

    def __hash__(self):
        return hash(type(self))

    def __str__(self):
        return self.__class__.__name__

    def make_node(self, x, y):
        x, y = as_sparse_variable(x), tensor.as_tensor_variable(y)

        #upcast the tensor. Is the cast of sparse done implemented?
        dtype = scalar.upcast(x.type.dtype, y.type.dtype)
        if y.type.dtype != dtype:
            y = tensor.cast(y, dtype)

        if x.type.dtype != y.type.dtype:
            raise NotImplementedError()
        # The magic number two here arises because L{scipy.sparse}
        # objects must be matrices (have dimension 2)
        # Broadcasting of the sparse matrix is not supported.
        assert y.type.ndim <= 2
        return gof.Apply(self, [x, y], [x.type()])

    def perform(self, node, (x, y), (out, )):
        assert _is_sparse(x) and _is_dense(y)
        if len(y.shape) == 0:
            out[0] = x.copy()
            out[0].data *= y
        elif len(y.shape) == 1:
            raise NotImplementedError()  # RowScale / ColScale
        elif len(y.shape) == 2:
            # if we have enough memory to fit y, maybe we can fit x.asarray()
            # too?
            #TODO: change runtime from O(M*N) to O(nonzeros)
            M, N = x.shape
            assert x.shape == y.shape

            if x.format == 'csc':
                x_data = x.data
                indices = x.indices
                indptr = x.indptr
                z = x.copy()
                z_data = z.data

                for j in xrange(0, N):
                    for i_idx in xrange(indptr[j], indptr[j + 1]):
                        i = indices[i_idx]
                        z_data[i_idx] *= y[i, j]
                out[0] = z
            elif x.format == 'csr':
                x_data = x.data
                indices = x.indices
                indptr = x.indptr
                z = x.copy()
                z_data = z.data

                for i in xrange(0, M):
                    for j_idx in xrange(indptr[i], indptr[i + 1]):
                        j = indices[j_idx]
                        z_data[j_idx] *= y[i, j]
                out[0] = z
            else:
                print >> sys.stderr, (
                    "WARNING: crappy implementation of MulSD"
                ), x.format
                out[0] = type(x)(x.toarray() * y)

    def grad(self, (x, y), (gz,)):
        assert _is_sparse_variable(x) and _is_dense_variable(y)
        assert _is_sparse_variable(gz)
        return y * gz, x * gz

    def infer_shape(self, node, shapes):
        return [shapes[0]]
mul_s_d = MulSD()


class MulSDCSC(gof.Op):
    # Multiplication of sparse matrix by a broadcasted dense vector
    # element wise.

    # :param a_data: Sparse matrix data.
    # :param a_indices: Sparse matrix indices.
    # :param a_indptr: Sparse matrix indptr.
    # :param b: Tensor type matrix.

    # :return: The multiplication of the two matrix element wise.

    # :note:
    # - `a_data`, `a_indices` and `a_indptr` must be the properties
    #   of a sparse matrix in csc format.
    # - The dtype of `a_data`, i.e. the dtype of the sparse matrix,
    #   cannot be a complex type.
    # - This op is used as an optimization of mul_s_d.

    def __eq__(self, other):
        return (type(self) == type(other))

    def __hash__(self):
        return hash(type(self))

    def make_node(self, a_data, a_indices, a_indptr, b):
        assert b.type.ndim == 2
        return gof.Apply(self, [a_data, a_indices, a_indptr, b],
                               [tensor.tensor(b.dtype, (False,))])

    def c_code_cache_version(self):
        return (1,)

    #def perform(self, node, (a_data, a_indices, a_indptr, b), (out,)):
    #    return NotImplementedError()

    def c_code(self, node, name, (_data, _indices, _indptr, _b,),
               (_zout, ), sub):

        if node.inputs[0].type.dtype in ('complex64', 'complex128'):
            raise NotImplementedError('Complex types are not supported for a')
        if node.inputs[3].type.dtype in ('complex64', 'complex128'):
            raise NotImplementedError('Complex types are not supported for b')

        return """
        if (%(_b)s->nd != 2) {
            PyErr_SetString(PyExc_NotImplementedError, "rank(b) != 2");
            %(fail)s;}
        if (%(_data)s->nd != 1) {
            PyErr_SetString(PyExc_NotImplementedError, "rank(data) != 1");
            %(fail)s;}
        if (%(_indices)s->nd != 1) {
            PyErr_SetString(PyExc_NotImplementedError, "rank(indices) != 1");
            %(fail)s;}
        if (%(_indptr)s->nd != 1) {
            PyErr_SetString(PyExc_NotImplementedError, "rank(indptr) != 1");
            %(fail)s;}

        if( %(_indices)s->descr->type_num != PyArray_INT32) {
        PyErr_SetString(PyExc_NotImplementedError, "C"); %(fail)s;}

        if( %(_indptr)s->descr->type_num != PyArray_INT32)
        {PyErr_SetString(PyExc_NotImplementedError, "D"); %(fail)s;}

        if (!%(_zout)s)
        {
            %(_zout)s = (PyArrayObject*) PyArray_SimpleNew(1,
                  %(_indices)s->dimensions, %(_b)s->descr->type_num);
        }

        if (%(_zout)s->dimensions[0] != %(_indices)s->dimensions[0])
        {
            PyErr_SetString(PyExc_NotImplementedError,
    "somehow _zout got the wrong size.. and I don't know how to resize it.");
            %(fail)s;
        }

        { //makes it compile even though labels jump over variable definitions.
            const npy_intp nnz = %(_indices)s->dimensions[0];
            //TODO: error checking with this
            const npy_intp N =  %(_indptr)s->dimensions[0]-1;

            const dtype_%(_data)s * const __restrict__ data = (dtype_%(_data)s*)%(_data)s->data;
            const npy_int32 * const __restrict__ indptr = (npy_int32 *)%(_indptr)s->data;
            const npy_int32 * const __restrict__ indices = (npy_int32 *)%(_indices)s->data;

            dtype_%(_zout)s * const __restrict__ zout = (dtype_%(_zout)s*)%(_zout)s->data;

            const npy_intp Sb = %(_b)s->strides[0];

            // loop over columns
            for (npy_int32 j = 0; j < N; ++j)
            {
                // for each non-null value in the sparse column
                for (npy_int32 i_idx = indptr[j]; i_idx < indptr[j+1]; ++i_idx)
                {
                    // extract row index of non-null value
                    npy_int32 i = indices[i_idx];

                    // extract i-th row of dense matrix
                    const dtype_%(_b)s* __restrict__ b_row = (dtype_%(_b)s*)(%(_b)s->data + Sb * i);

                    // write resulting gradient to sparse output
                    zout[i_idx] = data[i_idx] * b_row[j];
                }
            }
        }

        """ % dict(locals(), **sub)

    def __str__(self):
        return self.__class__.__name__
mul_s_d_csc = MulSDCSC()


class MulSDCSR(gof.Op):
    # Multiplication of sparse matrix by a broadcasted dense vector
    # element wise.

    # :param a_data: Sparse matrix data.
    # :param a_indices: Sparse matrix indices.
    # :param a_indptr: Sparse matrix indptr.
    # :param b: Tensor type matrix.

    # :return: The multiplication of the two matrix element wise.

    # :note:
    # - `a_data`, `a_indices` and `a_indptr` must be the properties
    #   of a sparse matrix in csr format.
    # - The dtype of `a_data`, i.e. the dtype of the sparse matrix,
    #   cannot be a complex type.
    # - This op is used as an optimization of mul_s_d.

    def __eq__(self, other):
        return (type(self) == type(other))

    def __hash__(self):
        return hash(type(self))

    def make_node(self, a_data, a_indices, a_indptr, b):
        assert b.type.ndim == 2
        return gof.Apply(self, [a_data, a_indices, a_indptr, b],
                               [tensor.tensor(b.dtype, (False,))])

    def c_code_cache_version(self):
        return (1,)

    #def perform(self, node, (a_data, a_indices, a_indptr, b), (out,)):
    #    return NotImplemented()

    def c_code(self, node, name, (_data, _indices, _indptr, _b,),
               (_zout, ), sub):

        if node.inputs[0].type.dtype in ('complex64', 'complex128'):
            raise NotImplementedError('Complex types are not supported for a')
        if node.inputs[3].type.dtype in ('complex64', 'complex128'):
            raise NotImplementedError('Complex types are not supported for b')

        return """
        if (%(_b)s->nd != 2) {
            PyErr_SetString(PyExc_NotImplementedError, "rank(b) != 2");
            %(fail)s;}
        if (%(_data)s->nd != 1) {
            PyErr_SetString(PyExc_NotImplementedError, "rank(data) != 1");
            %(fail)s;}
        if (%(_indices)s->nd != 1) {
            PyErr_SetString(PyExc_NotImplementedError, "rank(indices) != 1");
            %(fail)s;}
        if (%(_indptr)s->nd != 1) {
            PyErr_SetString(PyExc_NotImplementedError, "rank(indptr) != 1");
            %(fail)s;}

        if( %(_indices)s->descr->type_num != PyArray_INT32) {
        PyErr_SetString(PyExc_NotImplementedError, "C"); %(fail)s;}

        if( %(_indptr)s->descr->type_num != PyArray_INT32)
        {PyErr_SetString(PyExc_NotImplementedError, "D"); %(fail)s;}

        if (!%(_zout)s)
        {
            %(_zout)s = (PyArrayObject*) PyArray_SimpleNew(1,
                    %(_indices)s->dimensions, %(_b)s->descr->type_num);
        }

        if (%(_zout)s->dimensions[0] != %(_indices)s->dimensions[0])
        {
            PyErr_SetString(PyExc_NotImplementedError,
    "somehow _zout got the wrong size.. and I don't know how to resize it.");
            %(fail)s;
        }

        { //makes it compile even though labels jump over variable definitions.
            const npy_intp nnz = %(_indices)s->dimensions[0];
            //TODO: error checking with this
            const npy_intp N =  %(_indptr)s->dimensions[0]-1;

            const dtype_%(_data)s * const __restrict__ data = (dtype_%(_data)s*)%(_data)s->data;
            const npy_int32 * const __restrict__ indptr = (npy_int32 *)%(_indptr)s->data;
            const npy_int32 * const __restrict__ indices = (npy_int32 *)%(_indices)s->data;

            dtype_%(_zout)s * const __restrict__ zout = (dtype_%(_zout)s*)%(_zout)s->data;

            const npy_intp Sb = %(_b)s->strides[0];

            // loop over columns
            for (npy_int32 j = 0; j < N; ++j)
            {
                // extract i-th row of dense matrix
                const dtype_%(_b)s* __restrict__ b_row = (dtype_%(_b)s*)(%(_b)s->data + Sb * j);

                // for each non-null value in the sparse column
                for (npy_int32 i_idx = indptr[j]; i_idx < indptr[j+1]; ++i_idx)
                {
                    // extract row index of non-null value
                    npy_int32 i = indices[i_idx];

                    // write resulting gradient to sparse output
                    zout[i_idx] = data[i_idx] * b_row[i];
                }
            }
        }

        """ % dict(locals(), **sub)

    def __str__(self):
        return self.__class__.__name__
mul_s_d_csr = MulSDCSR()


class MulSV(gof.op.Op):
    """Multiplication of sparse matrix by a broadcasted dense vector
    element wise.

    :param x: Sparse matrix to multiply.
    :param y: Tensor broadcastable vector.

    :Return: The product x * y element wise.

    :note:
    - The grad implemented is regular, i.e. not structured.
    """

    def __eq__(self, other):
        return (type(self) == type(other))

    def __hash__(self):
        return hash(type(self))

    def make_node(self, x, y):
        x = as_sparse_variable(x)
        y = tensor.as_tensor_variable(y)

        assert y.type.ndim == 1

        if x.type.dtype != y.type.dtype:
            raise NotImplementedError()
        return gof.Apply(self,
                         [x, y],
                         [SparseType(dtype=x.type.dtype,
                                 format=x.type.format).make_variable()])

    def perform(self, node, (x, y), (out, )):
        assert _is_sparse(x) and not _is_sparse(y)
        assert x.shape[1] == y.shape[0]
        out[0] = x.__class__(x.toarray() * y)

    def grad(self, (x, y), (gz,)):
        assert _is_sparse_variable(x) and _is_dense_variable(y)
        assert _is_sparse_variable(gz)
        return mul_s_v(gz, y), sp_sum(x * gz, axis=0, sparse_grad=True)

    def infer_shape(self, node, ins_shapes):
        return [ins_shapes[0]]

    def __str__(self):
        return self.__class__.__name__
mul_s_v = MulSV()


class MulSVCSR(gof.Op):
    # Multiplication of sparse matrix by a broadcasted dense vector
    # element wise.

    # :param a_data: Sparse matrix data.
    # :param a_indices: Sparse matrix indices.
    # :param a_indptr: Sparse matrix indptr.
    # :param b: Tensor type matrix.

    # :return: The multiplication of the two matrix element wise.

    # :note:
    # - `a_data`, `a_indices` and `a_indptr` must be the properties
    #   of a sparse matrix in csr format.
    # - The dtype of `a_data`, i.e. the dtype of the sparse matrix,
    #   cannot be a complex type.
    # - This op is used as an optimization of MulSV.

    def __eq__(self, other):
        return (type(self) == type(other))

    def __hash__(self):
        return hash(type(self))

    def make_node(self, a_data, a_indices, a_indptr, b):
        assert b.type.ndim == 1
        return gof.Apply(self, [a_data, a_indices, a_indptr, b],
                               [tensor.tensor(b.dtype, (False,))])

    def c_code_cache_version(self):
        return (1,)

    def c_code(self, node, name, inputs, outputs, sub):
        _data, _indices, _indptr, _b, = inputs
        _zout, = outputs
        if node.inputs[0].type.dtype in ('complex64', 'complex128'):
            raise NotImplementedError('Complex types are not supported for a')
        if node.inputs[3].type.dtype in ('complex64', 'complex128'):
            raise NotImplementedError('Complex types are not supported for b')

        return """
        if (%(_b)s->nd != 1) {
            PyErr_SetString(PyExc_NotImplementedError, "rank(b) != 1");
            %(fail)s;
        }
        if (%(_data)s->nd != 1) {
            PyErr_SetString(PyExc_NotImplementedError, "rank(data) != 1");
            %(fail)s;
        }
        if (%(_indices)s->nd != 1) {
            PyErr_SetString(PyExc_NotImplementedError, "rank(indices) != 1");
            %(fail)s;
        }
        if (%(_indptr)s->nd != 1) {
            PyErr_SetString(PyExc_NotImplementedError, "rank(indptr) != 1");
            %(fail)s;
        }

        if( %(_indices)s->descr->type_num != PyArray_INT32) {
        PyErr_SetString(PyExc_NotImplementedError, "C"); %(fail)s;}

        if( %(_indptr)s->descr->type_num != PyArray_INT32)
        {PyErr_SetString(PyExc_NotImplementedError, "D"); %(fail)s;}

        if (!%(_zout)s
            || %(_zout)s->dimensions[0] != %(_indices)s->dimensions[0]
            || !PyArray_ISCONTIGUOUS(%(_zout)s))
        {
            Py_XDECREF(%(_zout)s);
            %(_zout)s = (PyArrayObject*) PyArray_SimpleNew(1,
                    %(_indices)s->dimensions, %(_b)s->descr->type_num);
        }

        { //makes it compile even though labels jump over variable definitions.
            const npy_intp nnz = %(_indices)s->dimensions[0];
            //TODO: error checking with this
            const npy_intp N =  %(_indptr)s->dimensions[0]-1;

            const dtype_%(_data)s * const __restrict__ data = (dtype_%(_data)s*)%(_data)s->data;
            const npy_int32 * const __restrict__ indptr = (npy_int32 *)%(_indptr)s->data;
            const npy_int32 * const __restrict__ indices = (npy_int32 *)%(_indices)s->data;

            const dtype_%(_b)s* __restrict__ Db = (dtype_%(_b)s*)%(_b)s->data;

            dtype_%(_zout)s * const __restrict__ zout = (dtype_%(_zout)s*)%(_zout)s->data;

            const npy_intp Sb = %(_b)s->strides[0] / %(_b)s->descr->elsize;

            // loop over rows
            for (npy_int32 j = 0; j < N; ++j)
            {
                // for each non-null value in the sparse column
                for (npy_int32 i_idx = indptr[j]; i_idx < indptr[j+1]; ++i_idx)
                {
                    // extract row index of non-null value
                    npy_int32 i = indices[i_idx];

                    zout[i_idx] = data[i_idx] * Db[i * Sb];
                }
            }
        }

        """ % dict(locals(), **sub)

    def __str__(self):
        return self.__class__.__name__
mul_s_v_csr = MulSVCSR()


def mul(x, y):
    """Multiply elementwise two matrices, at least one
    of which is sparse.

    This method will provide the right op according
    to the inputs.

    :param x: A matrix variable.
    :param y: A matrix variable.

    :return: `x` + `y`

    :note:
    - At least one of `x` and `y` must be a sparse matrix.
    - The grad is regular, i.e. not structured.
    """

    x = as_sparse_or_tensor_variable(x)
    y = as_sparse_or_tensor_variable(y)

    x_is_sparse_variable = _is_sparse_variable(x)
    y_is_sparse_variable = _is_sparse_variable(y)

    assert x_is_sparse_variable or y_is_sparse_variable
    if x_is_sparse_variable and y_is_sparse_variable:
        return mul_s_s(x, y)
    elif x_is_sparse_variable and not y_is_sparse_variable:
        return mul_s_d(x, y)
    elif y_is_sparse_variable and not x_is_sparse_variable:
        return mul_s_d(y, x)
    else:
        raise NotImplementedError()


class HStack(gof.op.Op):
    """Stack sparse matrices horizontally (column wise).

    :param blocks: Sequence of sparse array of compatible shape.
    :param format: String representing the output format. Default
                   is csc.
    :param dtype: Output dtype. Must be specified.

    :return: The concatenation of the sparse arrays column wise.

    :note:
    - The number of line of the sparse matrix must agree.
    - The grad implemented is regular, i.e. not structured.
    """

    def __init__(self, format=None, dtype=None):
        if format is None:
            self.format = 'csc'
        else:
            self.format = format

        if dtype is None:
            raise ValueError('The output dtype must be specified.')
        self.dtype = dtype

    def __eq__(self, other):
        return (type(self) == type(other) and
                self.format == other.format and
                self.dtype == other.dtype)

    def __hash__(self):
        return hash(type(self)) ^ hash(self.format) ^ hash(self.dtype)

    def make_node(self, *mat):
        if not mat:
            raise ValueError('Cannot join an empty list of sparses.')
        var = [as_sparse_variable(x) for x in mat]
        return gof.Apply(
            self, var,
            [SparseType(dtype=self.dtype, format=self.format).make_variable()])

    def perform(self, node, block, (out, )):
        for b in block:
            assert _is_sparse(b)
        out[0] = scipy.sparse.hstack(block, format=self.format,
                                     dtype=self.dtype)

    def grad(self, inputs, (gz, )):
        is_continuous = [(inputs[i].dtype in tensor.continuous_dtypes)
                         for i in range(len(inputs))]

        if _is_sparse_variable(gz):
            gz = DenseFromSparse()(gz)

        split = tensor.Split(len(inputs))(gz, 1,
                                          tensor.stack(
                                              *[x.shape[1]
                                                for x in inputs]))
        if not isinstance(split, list):
            split = [split]

        derivative = [SparseFromDense(self.format)(s) for s in split]

        def choose(continuous, derivative):
            if continuous:
                return derivative
            else:
                return None
        return [choose(c, d) for c, d in zip(is_continuous, derivative)]

    def infer_shape(self, node, ins_shapes):
        def _get(l):
            return l[1]
        d = sum(map(_get, ins_shapes))
        return [(ins_shapes[0][0], d)]

    def __str__(self):
        return "%s(%s,%s)" % (self.__class__.__name__, self.format, self.dtype)


def hstack(blocks, format=None, dtype=None):
    """Stack sparse matrices horizontally (column wise).

    This wrap the method hstack from scipy.

    :param blocks: List of sparse array of compatible shape.
    :param format: String representing the output format. Default
                   is csc.
    :param dtype: Output dtype.

    :return: The concatenation of the sparse array column wise.

    :note:
    - The number of line of the sparse matrix must agree.
    - The grad implemented is regular, i.e. not structured.
    """

    blocks = [as_sparse_variable(i) for i in blocks]
    if dtype is None:
        dtype = theano.scalar.upcast([i.dtype for i in blocks])
    return HStack(format=format, dtype=dtype)(*blocks)


class VStack(HStack):
    """Stack sparse matrices vertically (row wise).

    :param blocks: Sequence of sparse array of compatible shape.
    :param format: String representing the output format. Default
                   is csc.
    :param dtype: Output dtype. Must be specified.

    :return: The concatenation of the sparse arrays row wise.

    :note:
    - The number of column of the sparse matrix must agree.
    - The grad implemented is regular, i.e. not structured.
    """

    def perform(self, node, block, (out, )):
        for b in block:
            assert _is_sparse(b)
        out[0] = scipy.sparse.vstack(block, format=self.format,
                                     dtype=self.dtype)

    def grad(self, inputs, (gz, )):
        is_continuous = [(inputs[i].dtype in tensor.continuous_dtypes)
                        for i in range(len(inputs))]

        if _is_sparse_variable(gz):
            gz = DenseFromSparse()(gz)

        split = tensor.Split(len(inputs))(gz, 0,
                                          tensor.stack(
                                              *[x.shape[0]
                                                for x in inputs]))
        if not isinstance(split, list):
            split = [split]

        derivative = [SparseFromDense(self.format)(s) for s in split]

        def choose(continuous, derivative):
            if continuous:
                return derivative
            else:
                return None
        return [choose(c, d) for c, d in zip(is_continuous, derivative)]

    def infer_shape(self, node, ins_shapes):
        def _get(l):
            return l[0]
        d = sum(map(_get, ins_shapes))
        return [(d, ins_shapes[0][1])]


def vstack(blocks, format=None, dtype=None):
    """Stack sparse matrices vertically (row wise).

    This wrap the method vstack from scipy.

    :param blocks: List of sparse array of compatible shape.
    :param format: String representing the output format. Default
                   is csc.
    :param dtype: Output dtype.

    :return: The concatenation of the sparse array row wise.

    :note:
    - The number of column of the sparse matrix must agree.
    - The grad implemented is regular, i.e. not structured.
    """

    blocks = [as_sparse_variable(i) for i in blocks]
    if dtype is None:
        dtype = theano.scalar.upcast([i.dtype for i in blocks])
    return VStack(format=format, dtype=dtype)(*blocks)


class Remove0(gof.Op):
    """
    Remove explicit zeros from a sparse matrix, and resort indices
    """

    def __init__(self, inplace=False, *args, **kwargs):
        gof.Op.__init__(self, *args, **kwargs)
        self.inplace = inplace
        if self.inplace:
            self.destroy_map = {0: [0]}

    def __eq__(self, other):
        return type(self) == type(other) and self.inplace == other.inplace

    def __hash__(self):
        return 64153 ^ hash(type(self)) ^ hash(self.inplace)

    def __str__(self):
        l = []
        if self.inplace:
            l.append('inplace')
        return self.__class__.__name__ + '{%s}' % ', '.join(l)

    def make_node(self, x):
        return gof.Apply(self, [x], [x.type()])

    def perform(self, node, (x,), (z,)):
        if self.inplace:
            c = x
        else:
            c = x.copy()
        c.eliminate_zeros()
        z[0] = c

    def grad(self, (x,), (gz,)):
        return [gz]

    def infer_shape(self, node, i0_shapes):
        return i0_shapes
remove0 = Remove0()


# Probability
class Poisson(gof.op.Op):
    """Return a sparse having random values from a Poisson density
    with mean from the input.

    :param x: Sparse matrix.

    :return: A sparse matrix of random integers of a Poisson density
             with mean of `x` element wise.
    """

    def __eq__(self, other):
        return (type(self) == type(other))

    def __hash__(self):
        return hash(type(self))

    def make_node(self, x):
        x = as_sparse_variable(x)
        return gof.Apply(self, [x], [x.type()])

    def perform(self, node, (x, ), (out, )):
        assert _is_sparse(x)
        out[0] = x.copy()
        out[0].data = numpy.asarray(numpy.random.poisson(out[0].data),
                                    dtype=x.dtype)
        out[0].eliminate_zeros()

    def grad(self, inputs, outputs_gradients):
        return [None]

    def infer_shape(self, node, ins_shapes):
        return ins_shapes

    def __str__(self):
        return self.__class__.__name__
poisson = Poisson()


class Binomial(gof.op.Op):
    """Return a sparse matrix having random values from a binomial
    density having number of experiment `n` and probability of succes
    `p`.

    :param n: Tensor scalar representing the number of experiment.
    :param p: Tensor scalar representing the probability of success.
    :param shape: Tensor vector for the output shape.

    :return: A sparse matrix of integers representing the number
             of success.
    """

    def __init__(self, format, dtype):
        self.format = format
        self.dtype = dtype

    def __eq__(self, other):
        return ((type(self) == type(other)) and
                self.format == other.format and
                self.dtype == other.dtype)

    def __hash__(self):
        return hash(type(self)) ^ hash(self.format) ^ hash(self.dtype)

    def make_node(self, n, p, shape):
        n = tensor.as_tensor_variable(n)
        p = tensor.as_tensor_variable(p)
        shape = tensor.as_tensor_variable(shape)
        return gof.Apply(self, [n, p, shape], [SparseType(dtype=self.dtype,
                                 format=self.format).make_variable()])

    def perform(self, node, (n, p, shape, ), (out, )):
        binomial = numpy.random.binomial(n, p, size=shape)
        csx_matrix = getattr(scipy.sparse, self.format + '_matrix')
        out[0] = csx_matrix(binomial, dtype=self.dtype)

    def grad(self, (n, p, shape, ), (gz,)):
        return None, None, None

    def infer_shape(self, node, ins_shapes):
        return [(node.inputs[2][0], node.inputs[2][1])]

    def __str__(self):
        return self.__class__.__name__

csr_fbinomial = Binomial('csr', 'float32')
csc_fbinomial = Binomial('csc', 'float32')
csr_dbinomial = Binomial('csr', 'float64')
csc_dbinomial = Binomial('csc', 'float64')


class Multinomial(gof.op.Op):
    """Return a sparse matrix having random values from a multinomial
    density having number of experiment `n` and probability of succes
    `p`.

    :param n: Tensor type vector or scalar representing the number of
              experiment for each row. If `n` is a scalar, it will be
              used for each row.
    :param p: Sparse matrix of probability where each row is a probability
              vector representing the probability of succes. N.B. Each row
              must sum to one.

    :return: A sparse matrix of random integers from a multinomial density
             for each row.

    :note: It will works only if `p` have csr format.
    """

    def __eq__(self, other):
        return (type(self) == type(other))

    def __hash__(self):
        return hash(type(self))

    def make_node(self, n, p):
        n = tensor.as_tensor_variable(n)
        p = as_sparse_variable(p)

        return gof.Apply(self, [n, p], [p.type()])

    def perform(self, node, (n, p), (out, )):
        assert _is_sparse(p)

        if p.format != 'csr':
            raise NotImplemented()

        out[0] = p.copy()

        if n.ndim == 0:
            for i in xrange(p.shape[0]):
                k, l = p.indptr[i], p.indptr[i + 1]
                out[0].data[k:l] = numpy.random.multinomial(n, p.data[k:l])
        elif n.ndim == 1:
            if n.shape[0] != p.shape[0]:
                raise ValueError('The number of element of n must be '
                                 'the same as the number of row of p.')
            for i in xrange(p.shape[0]):
                k, l = p.indptr[i], p.indptr[i + 1]
                out[0].data[k:l] = numpy.random.multinomial(n[i], p.data[k:l])

    def grad(self, inputs, outputs_gradients):
        return [None, None]

    def infer_shape(self, node, ins_shapes):
        return [ins_shapes[1]]

    def __str__(self):
        return self.__class__.__name__
multinomial = Multinomial()


# Structured monoid
def structured_monoid(tensor_op):
    # Generic operation to perform many kinds of monoid element-wise
    # operations on the non-zeros of a sparse matrix.

    # The first parameter must always be a sparse matrix. The other parameters
    # must be scalars which will be passed as argument to the tensor_op.

    def decorator(f):
        def wrapper(*args):
            x = as_sparse_variable(args[0])

            xs = [scalar.as_scalar(arg) for arg in args[1:]]

            data, ind, ptr, shape = csm_properties(x)

            data = tensor_op(data, *xs)

            return CSM(x.format)(data, ind, ptr, shape)
        return wrapper
    return decorator


@structured_monoid(tensor.nnet.sigmoid)
def structured_sigmoid(x):
    """structured elemwise sigmoid.
    """
    # see decorator for function body


@structured_monoid(tensor.exp)
def structured_exp(x):
    """structured elemwise exponential.
    """
    # see decorator for function body


@structured_monoid(tensor.log)
def structured_log(x):
    """structured elemwise logarithm.
    """
    # see decorator for function body


@structured_monoid(tensor.pow)
def structured_pow(x, y):
    """structured elemwise power of sparse matrix
    x by scalar y.
    """
    # see decorator for function body


@structured_monoid(tensor.minimum)
def structured_minimum(x, y):
    """structured elemwise minimum of sparse matrix
    x by scalar y.
    """
    # see decorator for function body


@structured_monoid(tensor.maximum)
def structured_maximum(x, y):
    """structured elemwise maximum of sparse matrix
    x by scalar y.
    """
    # see decorator for function body


@structured_monoid(tensor.add)
def structured_add(x):
    """structured addition of sparse matrix
    x and scalar y.
    """
    # see decorator for function body


# Dot
class StructuredDot(gof.Op):
    """Structured Dot is like dot, except that only the
    gradient wrt non-zero elements of the sparse matrix
    `a` are calculated and propagated.

    The output is presumed to be a dense matrix, and is represented by a
    TensorType instance.

    :param a: A sparse matrix.
    :param b: A sparse or dense matrix.

    :return: The dot product of `a` and `b`.

    :note:
    - The grad implemented is structured.
    """

    def __eq__(self, other):
        return (type(self) == type(other))

    def __hash__(self):
        return hash(type(self))

    def __str__(self):
        return self.__class__.__name__

    def make_node(self, a, b):
        if not _is_sparse_variable(a):
            raise TypeError('First argument must be of type SparseVariable '
                            'or SparseConstant')
        dtype_out = scalar.upcast(a.type.dtype, b.type.dtype)
        if b.type.ndim != 2:
            raise NotImplementedError('non-matrix b')

        if _is_sparse_variable(b):
            return gof.Apply(self, [a, b],
                             [SparseType(a.type.format, dtype_out)()])
        else:
            return gof.Apply(self, [a, b],
                             [tensor.tensor(dtype_out,
                                            (False, b.type.broadcastable[1]))])

    def perform(self, node, (a, b), (out,)):
        if a.shape[1] != b.shape[0]:
            raise ValueError('shape mismatch in StructuredDot.perform',
                             (a.shape, b.shape))

        #variable = a.dot(b)  # deprecated
        variable = a * b
        if isinstance(node.outputs[0].type, SparseType):
            assert _is_sparse(variable)
            out[0] = variable
            return

        assert _is_dense(variable)  # scipy 0.7 automatically converts to dense

        # dot of an NxM sparse matrix, with a Mx1 dense matrix, returns vector
        # not matrix
        if variable.ndim == 1:
            variable = numpy.expand_dims(variable, 1)
        elif variable.ndim != 2:
            raise Exception('Output of structured dot should be a matrix '
                            '(ndim=2)')

        assert variable.ndim == 2

        if variable.shape != (a.shape[0], b.shape[1]):
            if b.shape[0] == 1:
                raise Exception("a.shape=%s, b.shape=%s, "
                                "variable.shape=%s ??? This is probably "
                                "because scipy.csc_matrix dot has a bug "
                                "with singleton dimensions (i.e. "
                                "b.shape[0]=1), for scipy 0.6. Use scipy "
                                "0.7. NB you have scipy version %s" %
                                (a.shape, b.shape, variable.shape,
                                 scipy.__version__))
            else:
                raise Exception("a.shape=%s, b.shape=%s, variable.shape=%s "
                                " ??? I have no idea why")

        #The cast is needed as otherwise we hit the bug mentioned into
        #theano._asarray function documentation.
        out[0] = theano._asarray(variable, str(variable.dtype))

    def grad(self, (a, b), (g_out,)):
        # a is sparse, b is dense, g_out is dense
        # ga = g_out x b.T
        # gb = a.T x g_out
        return [structured_dot_grad(a, b, g_out), structured_dot(a.T, g_out)]

    def infer_shape(self, node, shapes):
        return [(shapes[0][0], shapes[1][1])]

_structured_dot = StructuredDot()


def structured_dot(x, y):
    """Structured Dot is like dot, except that only the
    gradient wrt non-zero elements of the sparse matrix
    `a` are calculated and propagated.

    The output is presumed to be a dense matrix, and is represented by a
    TensorType instance.

    :param a: A sparse matrix.
    :param b: A sparse or dense matrix.

    :return: The dot product of `a` and `b`.

    :note:
    - The grad implemented is structured.
    """

    # @todo: Maybe the triple-transposition formulation (when x is dense)
    # is slow. See if there is a direct way to do this.
    # (JB 20090528: Transposing tensors and sparse matrices is constant-time,
    # inplace, and fast.)

    if hasattr(x, 'getnnz'):
        x = as_sparse_variable(x)
    if hasattr(y, 'getnnz'):
        y = as_sparse_variable(y)

    x_is_sparse_variable = _is_sparse_variable(x)
    y_is_sparse_variable = _is_sparse_variable(y)
    if not x_is_sparse_variable and not y_is_sparse_variable:
        raise TypeError('structured_dot requires at least one sparse argument')

    if x_is_sparse_variable:
        return _structured_dot(x, y)
    else:
        assert y_is_sparse_variable
        return _structured_dot(y.T, x.T).T


class StructuredDotCSC(gof.Op):
    # Structured Dot CSC is like dot, except that only the
    # gradient wrt non-zero elements of the sparse matrix
    # `a` are calculated and propagated.

    # The output is presumed to be a dense matrix, and is represented by a
    # TensorType instance.

    # :param a: A sparse matrix in csc format.
    # :param b: A sparse or dense matrix.

    # :return: The dot product of `a` and `b`.

    # :note:
    # - The grad implemented is structured.
    # - This op is used as an optimization for StructuredDot.

    def __eq__(self, other):
        return (type(self) == type(other))

    def __hash__(self):
        return hash(type(self))

    def __str__(self):
        return self.__class__.__name__

    def make_node(self, a_val, a_ind, a_ptr, a_nrows, b):
        dtype_out = scalar.upcast(a_val.type.dtype, b.type.dtype)
        r = gof.Apply(self, [a_val, a_ind, a_ptr, a_nrows, b],
                [tensor.tensor(dtype_out, (False, b.type.broadcastable[1]))])
        return r

    def perform(self, node, (a_val, a_ind, a_ptr, a_nrows, b), (out,)):
        a = scipy.sparse.csc_matrix((a_val, a_ind, a_ptr),
                (a_nrows, b.shape[0]),
                copy=False)
        #out[0] = a.dot(b)
        out[0] = theano._asarray(a * b, dtype=node.outputs[0].type.dtype)
        assert _is_dense(out[0])  # scipy 0.7 automatically converts to dense

    def c_code(self, node, name, (a_val, a_ind, a_ptr, a_nrows, b), (z,), sub):
        # C-implementation of the dot product of the sparse matrix A and matrix
        # B.
        # @param a_val: non-zero values of the sparse matrix
        # @param a_ind: column indices of the non-null values (.indices of a
        # scipy.csc_matrix)
        # @param a_ptr: a_ptr indicates col indices for col. i are in the range
        # a_ptr[i]:a_ptr[i+1]
        # @param n_rows: number of rows of sparse matrix
        # @param b: dense matrix to perform dot product with, as in dot(a, b)
        # @param z: return value
        # @param sub: TODO, not too sure, something to do with weave probably


        if node.inputs[0].type.dtype in ('complex64', 'complex128'):
            raise NotImplementedError('Complex types are not supported for a_val')
        if node.inputs[4].type.dtype in ('complex64', 'complex128'):
            raise NotImplementedError('Complex types are not supported for b')

        typenum_z = node.outputs[0].type.dtype_specs()[-1]  # retrieve dtype number
        typenum_a_val = node.inputs[0].type.dtype_specs()[-1]  # retrieve dtype number
        typenum_b = node.inputs[4].type.dtype_specs()[-1]  # retrieve dtype number

        rval = """

        if (%(a_val)s->nd != 1) {PyErr_SetString(PyExc_NotImplementedError, "rank(a_val) != 1"); %(fail)s;}
        if (%(a_ind)s->nd != 1) {PyErr_SetString(PyExc_NotImplementedError, "rank(a_ind) != 1"); %(fail)s;}
        if (%(a_ptr)s->nd != 1) {PyErr_SetString(PyExc_NotImplementedError, "rank(a_ptr) != 1"); %(fail)s;}
        if (%(a_nrows)s->nd != 0) {PyErr_SetString(PyExc_NotImplementedError, "rank(nrows) != 0"); %(fail)s;}
        if (%(b)s->nd != 2) {PyErr_SetString(PyExc_NotImplementedError, "rank(b) != 2"); %(fail)s;}

        if (%(a_val)s->descr->type_num != %(typenum_a_val)s) {
        PyErr_SetString(PyExc_NotImplementedError, "Invalid type for a_val"); %(fail)s;}

        if (%(b)s->descr->type_num != %(typenum_b)s) {
        PyErr_SetString(PyExc_NotImplementedError, "Invalid type for b"); %(fail)s;}

        if (%(a_ind)s->descr->type_num != PyArray_INT32) {
        PyErr_SetString(PyExc_NotImplementedError, "a_ind dtype not INT32"); %(fail)s;}

        if (%(a_ptr)s->descr->type_num != PyArray_INT32)
        {PyErr_SetString(PyExc_NotImplementedError, "a_ptr dtype not INT32"); %(fail)s;}

        if (%(a_nrows)s->descr->type_num != PyArray_INT32)
        {PyErr_SetString(PyExc_NotImplementedError, "a_nrows dtype not INT32"); %(fail)s;}

        if (%(a_val)s->dimensions[0] != %(a_ind)s->dimensions[0])
        {PyErr_SetString(PyExc_NotImplementedError, "a_val and a_ind have different lengths"); %(fail)s;}

        if (%(a_ptr)s->dimensions[0] != %(b)s->dimensions[0]+1)
        {PyErr_SetString(PyExc_NotImplementedError, "a's number of columns doesn't match b's rows"); %(fail)s;}

        if ((!%(z)s)
            || (%(z)s->dimensions[0] != ((npy_int32 *)%(a_nrows)s->data)[0])
            || (%(z)s->dimensions[1] != %(b)s->dimensions[1])
            )
        {
            {Py_XDECREF(%(z)s);}
            npy_intp dims[] = {0, 0};
            dims[0] = ((npy_int32 *)%(a_nrows)s->data)[0];
            dims[1] = %(b)s->dimensions[1];
            %(z)s = (PyArrayObject*) PyArray_SimpleNew(2, dims, %(typenum_z)s);
        }

        {
            // sparse array has size MxK, dense KxN, output MxN
            npy_intp M = %(z)s->dimensions[0];
            npy_intp N = %(z)s->dimensions[1];
            npy_intp K = %(b)s->dimensions[0];

            // strides tell you how many bytes to skip to go to next column/row entry
            npy_intp Szm = %(z)s->strides[0] / %(z)s->descr->elsize;
            npy_intp Szn = %(z)s->strides[1] / %(z)s->descr->elsize;
            //npy_intp Sbm = %(b)s->strides[0] / %(b)s->descr->elsize;
            npy_intp Sbn = %(b)s->strides[1] / %(b)s->descr->elsize;
            npy_intp Sval = %(a_val)s->strides[0] / %(a_val)s->descr->elsize;
            npy_intp Sind = %(a_ind)s->strides[0] / %(a_ind)s->descr->elsize;
            npy_intp Sptr = %(a_ptr)s->strides[0] / %(a_ptr)s->descr->elsize;

            // pointers to access actual data in the arrays passed as params.
            dtype_%(z)s*     __restrict__ Dz   = (dtype_%(z)s*)%(z)s->data;
            const dtype_%(a_val)s* __restrict__ Dval = (dtype_%(a_val)s*)%(a_val)s->data;
            const npy_int32 * __restrict__ Dind = (npy_int32*)%(a_ind)s->data;
            const npy_int32 * __restrict__ Dptr = (npy_int32*)%(a_ptr)s->data;

            //npy_intp nnz = %(a_ind)s->dimensions[0];

            //clear the output array
            memset(Dz, 0, M*N*sizeof(dtype_%(z)s));

            //iterate over the sparse array, making the most of an entry wherever we find it.
            //
            // Normal matrix matrix multiply: A MxK, B KxN =>  Z = AB
            // for m
            //   for n
            //     for k
            //        z[m, n] += a[m, k] * b[k, n]
            // Here instead: Z =
            // for k
            //   for m (sparse)
            //     for n
            //        z[m, n] += a[m, k] * b[k, n]

            // loop over inner dimension
            for (npy_int32 k = 0; k < K; ++k)
            {
                // get pointer to k-th row of dense matrix
                const dtype_%(b)s* __restrict__ bk = (dtype_%(b)s*)(%(b)s->data + %(b)s->strides[0] * k);

                // loop over sparse column indices through index pointer array
                // (amounts to looping over rows M of sparse matrix)

                for (npy_int32 m_idx = Dptr[k * Sptr]; m_idx < Dptr[(k+1) * Sptr]; ++m_idx)
                {
                    npy_int32 m = Dind[m_idx * Sind]; // row index of non-null value for column K
                    const dtype_%(a_val)s Amk = Dval[m_idx * Sval]; // actual value at that location

                    // pointer to m-th row of the output matrix Z
                    dtype_%(z)s* __restrict__ zm = (dtype_%(z)s*)(%(z)s->data + %(z)s->strides[0] * m);

                    //RESOLVE: a.shape[0] equals z.shape[0], why is this not an equality constraint?
                    if (m >= %(z)s->dimensions[0])
                    {PyErr_SetString(PyExc_NotImplementedError, "illegal row index in a"); %(fail)s;}

                    // loop over final dimension (cols of dense matrix) and perform dot product
                    if ((Szn == 1) && (Sbn == 1)) {
                        for(npy_int32 n = 0; n < N; ++n)
                        {
                            zm[n] += Amk * bk[n];
                        }
                    }
                    else
                    {
                        for(npy_int32 n = 0; n < N; ++n)
                        {
                            zm[n*Szn] += Amk * bk[n*Sbn];
                        }
                    }
                }
            }
        }
        """ % dict(locals(), **sub)

        return rval

    def c_code_cache_version(self):
        return (2,)
sd_csc = StructuredDotCSC()


class StructuredDotCSR(gof.Op):
    # Structured Dot CSR is like dot, except that only the
    # gradient wrt non-zero elements of the sparse matrix
    # `a` are calculated and propagated.

    # The output is presumed to be a dense matrix, and is represented by a
    # TensorType instance.

    # :param a: A sparse matrix in csr format.
    # :param b: A sparse or dense matrix.

    # :return: The dot product of `a` and `b`.

    # :note:
    # - The grad implemented is structured.
    # - This op is used as an optimization for StructuredDot.

    def __eq__(self, other):
        return (type(self) == type(other))

    def __hash__(self):
        return hash(type(self))

    def __str__(self):
        return self.__class__.__name__

    def make_node(self, a_val, a_ind, a_ptr, b):
        self.dtype_out = scalar.upcast(a_val.type.dtype, b.type.dtype)
        r = gof.Apply(self, [a_val, a_ind, a_ptr, b],
                [tensor.tensor(self.dtype_out, (False,
                                                b.type.broadcastable[1]))])
        return r

    def perform(self, node, (a_val, a_ind, a_ptr, b), (out,)):
        a = scipy.sparse.csr_matrix((a_val, a_ind, a_ptr),
                (len(a_ptr) - 1, b.shape[0]),
                copy=True)  # use view_map before setting this to False
        #out[0] = a.dot(b)
        out[0] = a * b
        # scipy 0.7 automatically converts to dense, but not .6 sometimes
        assert _is_dense(out[0])

    def c_code(self, node, name, (a_val, a_ind, a_ptr, b), (z,), sub):
        """
        C-implementation of the dot product of the sparse matrix A and matrix
        B.
        @param a_val: non-zero values of the sparse matrix
        @param a_ind: column indices of the non-null values (.indices of a
        scipy.csc_matrix)
        @param a_ptr: a_ptr indicates col indices for col. i are in the range
        a_ptr[i]:a_ptr[i+1]
        @param n_cols: number of columns of sparse matrix
        @param b: dense matrix to perform dot product with, as in dot(a, b)
        @param z: return value
        @param sub: TODO, not too sure, something to do with weave probably
        """
        # retrieve dtype number
        typenum_z = tensor.TensorType(self.dtype_out, []).dtype_specs()[-1]
        if node.inputs[0].type.dtype in ('complex64', 'complex128'):
            raise NotImplementedError('Complex types are not supported for a_val')
        if node.inputs[3].type.dtype in ('complex64', 'complex128'):
            raise NotImplementedError('Complex types are not supported for b')

        return """
        if (%(a_val)s->nd != 1) {PyErr_SetString(PyExc_NotImplementedError, "rank(a_val) != 1"); %(fail)s;}
        if (%(a_ind)s->nd != 1) {PyErr_SetString(PyExc_NotImplementedError, "rank(a_ind) != 1"); %(fail)s;}
        if (%(a_ptr)s->nd != 1) {PyErr_SetString(PyExc_NotImplementedError, "rank(a_ptr) != 1"); %(fail)s;}
        if (%(b)s->nd != 2) {PyErr_SetString(PyExc_NotImplementedError, "rank(b) != 2"); %(fail)s;}

        if (%(a_ind)s->descr->type_num != PyArray_INT32) {
        PyErr_SetString(PyExc_NotImplementedError, "a_ind dtype not INT32"); %(fail)s;}

        if (%(a_ptr)s->descr->type_num != PyArray_INT32)
        {PyErr_SetString(PyExc_NotImplementedError, "a_ptr dtype not INT32"); %(fail)s;}

        if (%(a_val)s->dimensions[0] != %(a_ind)s->dimensions[0])
        {PyErr_SetString(PyExc_NotImplementedError, "a_val and a_ind have different lengths"); %(fail)s;}

        if ((!%(z)s)
            || (%(z)s->dimensions[0] != %(a_ptr)s->dimensions[0]-1) //a's rows
            || (%(z)s->dimensions[1] != %(b)s->dimensions[1])       //b's columns
            )
        {
            {Py_XDECREF(%(z)s);}
            npy_intp dims[] = {0, 0};
            dims[0] = %(a_ptr)s->dimensions[0]-1;
            dims[1] = %(b)s->dimensions[1];
            %(z)s = (PyArrayObject*) PyArray_SimpleNew(2, dims, %(typenum_z)s);
        }

        {
            // sparse array has size MxK, dense KxN, output MxN
            npy_intp M = %(z)s->dimensions[0];
            npy_intp N = %(z)s->dimensions[1];
            npy_intp K = %(b)s->dimensions[0];

            // strides tell you how many bytes to skip to go to next column/row entry
            npy_intp Szm = %(z)s->strides[0] / %(z)s->descr->elsize;
            npy_intp Szn = %(z)s->strides[1] / %(z)s->descr->elsize;
            npy_intp Sbm = %(b)s->strides[0] / %(b)s->descr->elsize;
            npy_intp Sbn = %(b)s->strides[1] / %(b)s->descr->elsize;
            npy_intp Sval = %(a_val)s->strides[0] / %(a_val)s->descr->elsize;
            npy_intp Sind = %(a_ind)s->strides[0] / %(a_ind)s->descr->elsize;
            npy_intp Sptr = %(a_ptr)s->strides[0] / %(a_ptr)s->descr->elsize;

            // pointers to access actual data in the arrays passed as params.
            dtype_%(z)s* __restrict__ Dz = (dtype_%(z)s*)%(z)s->data;
            const dtype_%(a_val)s* __restrict__ Dval = (dtype_%(a_val)s*)%(a_val)s->data;
            const npy_int32 * __restrict__ Dind = (npy_int32*)%(a_ind)s->data;
            const npy_int32 * __restrict__ Dptr = (npy_int32*)%(a_ptr)s->data;

            //npy_intp nnz = %(a_ind)s->dimensions[0];

            //clear the output array
            memset(Dz, 0, M*N*sizeof(dtype_%(z)s));

            //iterate over the sparse array, making the most of an entry wherever we find it.
            // Normal matrix matrix multiply:
            // for m
            //   for n
            //     for k
            //        z[m, n] += a[m, k] * b[k, n]
            // Here instead:
            // for m
            //   for k (sparse)
            //     for n
            //        z[m, n] += a[m, k] * b[k, n]

            // loop over inner dimension
            for (npy_int64 m = 0; m < M; ++m)
            {
                // pointer to m-th row of the output matrix Z
                dtype_%(z)s* __restrict__ zm = (dtype_%(z)s*)(%(z)s->data + %(z)s->strides[0] * m);

                // loop over sparse rows indices through index pointer array
                // (amounts to looping over cols k of sparse matrix)
                for (npy_int32 k_idx = Dptr[m * Sptr]; k_idx < Dptr[(m+1) * Sptr]; ++k_idx)
                {
                    npy_int32 k = Dind[k_idx * Sind]; // col index of non-null value for row m
                    const dtype_%(a_val)s Amk = Dval[k_idx * Sval]; // actual value at that location

                    // get pointer to k-th row of dense matrix
                    const dtype_%(b)s* __restrict__ bk = (dtype_%(b)s*)(%(b)s->data + %(b)s->strides[0] * k);

                    // loop over final dimension (cols of dense matrix) and perform dot product
                    for(npy_int32 n = 0; n < N; ++n)
                    {
                        zm[n*Szn] += Amk * bk[n*Sbn];
                    }
                }
            }
        }

        """ % dict(locals(), **sub)

    def c_code_cache_version(self):
        return (1,)
sd_csr = StructuredDotCSR()


class SamplingDot(gof.op.Op):
    """Operand for calculating the dot product dot(`x`, `y`.T) = `z` when you
    only want to calculate a subset of `z`.

    It is equivalent to `p` o (`x` . `y`.T) where o is the element-wise
    product, `x` and `y` operands of the dot product and `p` is a matrix that
    contains 1 when the corresponding element of `z` should be calculated
    and 0 when it shouldn't. Note that SamplingDot has a different interface
    than `dot` because SamplingDot requires `x` to be a `m`x`k` matrix while
    `y` is a `n`x`k` matrix instead of the usual `k`x`n` matrix.

    .. note::

        It will work if the pattern is not binary value, but if the
        pattern doesn't have a high sparsity proportion it will be slower
        then a more optimized dot followed by a normal elemwise
        multiplication.

    :param x: Tensor matrix.
    :param y: Tensor matrix.
    :param p: Sparse matrix in csr format.

    :return: A dense matrix containing the dot product of `x` by `y`.T only
             where `p` is 1.

    :note:
    - The grad implemented is regular, i.e. not structured.
    """

    def __eq__(self, other):
        return type(self) == type(other)

    def __hash__(self):
        return hash(type(self))

    def make_node(self, x, y, p):
        x = tensor.as_tensor_variable(x)
        y = tensor.as_tensor_variable(y)
        p = as_sparse_variable(p)

        if not _is_sparse_variable(p):
            raise TypeError(p)

        #TODO: use it.
        dtype_out = scalar.upcast(x.type.dtype, y.type.dtype, p.type.dtype)

        return gof.Apply(self, [x, y, p], [p.type()])

    def perform(self, node, (x, y, p), (out,)):
        if _is_sparse(x):
            raise TypeError(x)

        if _is_sparse(y):
            raise TypeError(y)

        if not _is_sparse(p):
            raise TypeError(p)

        out[0] = p.__class__(p.multiply(numpy.dot(x, y.T)))

    def grad(self, (x, y, p), (gz,)):
        rval = [
            dot(p * gz, y),
            dot((p * gz).T, x),
            None
        ]

        return rval

    def infer_shape(self, node, ins_shapes):
        return [ins_shapes[2]]

    def __str__(self):
        return self.__class__.__name__
sampling_dot = SamplingDot()


class SamplingDotCSR(gof.Op):
    # Operand optimized for calculating the dot product dot(`x`, `y`.T) = `z`
    # when you only want to calculate a subset of `z`.

    # It is equivalent to `p` o (`x` . `y`.T) where o is the element-wise
    # product, `x` and `y` operands of the dot product and `p` is a matrix
    # that contains 1 when the corresponding element of `z` should be
    # calculated and 0 when it shouldn't. Note that SamplingDot has a different
    # interface than `dot` because SamplingDot requires `x` to be a `m`x`k`
    # matrix while `y` is a `n`x`k` matrix instead of the usual `k`x`n` matrix.

    # .. note::

    #     It will work if the pattern is not binary value, but if the
    #     pattern doesn't have a high sparsity proportion it will be slower
    #     then a more optimized dot followed by a normal elemwise
    #     multiplication.

    # :param x: Tensor matrix.
    # :param y: Tensor matrix.
    # :param p_data: Sparse matrix data.
    # :param p_ind: Sparse matrix indices.
    # :param p_ptr: Sparse matric indptr.
    # :param p_ncols: Sparse matrix number of columns.

    # :return: A dense matrix containing the dot product of `x` by `y`.T only
    #          where `p` is 1.

    # :note:
    # - If we have the input of mixed dtype, we insert cast elemwise
    #   in the graph to be able to call blas function as they don't
    #   allow mixed dtype.
    # - This op is used as an optimization for SamplingDot.

    def __eq__(self, other):
        return type(self) == type(other)

    def __hash__(self):
        return hash(type(self))

    def __str__(self):
        return 'SamplingDot{Csr}'

    def make_node(self, x, y, p_data, p_ind, p_ptr, p_ncols):
        x = tensor.as_tensor_variable(x)
        y = tensor.as_tensor_variable(y)
        p_data = tensor.as_tensor_variable(p_data)
        p_ind = tensor.as_tensor_variable(p_ind)
        p_ptr = tensor.as_tensor_variable(p_ptr)
        p_ncols = tensor.as_tensor_variable(p_ncols)

        assert p_ncols.dtype == 'int32'

        dtype_out = scalar.upcast(x.type.dtype, y.type.dtype,
                                  p_data.type.dtype)
        dot_out = scalar.upcast(x.type.dtype, y.type.dtype)

        # We call blas ?dot function that take only param of the same type
        x = tensor.cast(x, dot_out)
        y = tensor.cast(y, dot_out)

        return gof.Apply(self, [x, y, p_data, p_ind, p_ptr, p_ncols], [
            tensor.tensor(dtype=dtype_out, broadcastable=(False,)),
            tensor.tensor(dtype=p_ind.type.dtype, broadcastable=(False,)),
            tensor.tensor(dtype=p_ptr.type.dtype, broadcastable=(False,))
        ])

    def c_support_code(self):
        return blas.blas_header_text()

    def c_libraries(self):
        return blas.ldflags()

    def c_compile_args(self):
        return blas.ldflags(libs=False, flags=True)

    def c_lib_dirs(self):
        return blas.ldflags(libs=False, libs_dir=True)

    def c_header_dirs(self):
        return blas.ldflags(libs=False, include_dir=True)

    def c_code(self, node, name, inputs, outputs, sub):
        x, y, p_data, p_ind, p_ptr, p_ncols = inputs
        z_data, z_ind, z_ptr = outputs
        if node.inputs[0].type.dtype in ('complex64', 'complex128'):
            raise NotImplementedError('Complex types are not supported for x')
        if node.inputs[1].type.dtype in ('complex64', 'complex128'):
            raise NotImplementedError('Complex types are not supported for y')
        if node.inputs[2].type.dtype in ('complex64', 'complex128'):
            raise NotImplementedError(
                'Complex types are not supported for pattern')

        dot_out = scalar.upcast(node.inputs[0].type.dtype,
                                node.inputs[1].type.dtype)

        if dot_out == "float32":
            conv_type = "float"
            cdot = "sdot_"
        else:
            conv_type = "double"
            cdot = "ddot_"

        # retrieve dtype number
        typenum_x = node.inputs[0].type.dtype_specs()[-1]
        typenum_y = node.inputs[1].type.dtype_specs()[-1]
        typenum_p = node.inputs[2].type.dtype_specs()[-1]
        typenum_zd = tensor.TensorType(node.outputs[0].dtype,
                                       []).dtype_specs()[-1]
        typenum_zi = tensor.TensorType(node.outputs[1].dtype,
                                       []).dtype_specs()[-1]
        typenum_zp = tensor.TensorType(node.outputs[2].dtype,
                                       []).dtype_specs()[-1]

        rval = """
        if (%(x)s->nd != 2) {
PyErr_SetString(PyExc_NotImplementedError, "rank(x) != 2"); %(fail)s;}
        if (%(y)s->nd != 2) {
PyErr_SetString(PyExc_NotImplementedError, "rank(y) != 2"); %(fail)s;}

        if (%(x)s->descr->type_num != %(typenum_x)s) {
            PyErr_SetString(PyExc_NotImplementedError,
                            "Invalid type for x");
            %(fail)s;}

        if (%(y)s->descr->type_num != %(typenum_y)s) {
            PyErr_SetString(PyExc_NotImplementedError,
                            "Invalid type for y");
            %(fail)s;}

        if (%(p_data)s->descr->type_num != %(typenum_p)s) {
            PyErr_SetString(PyExc_NotImplementedError,
                            "Invalid type for pattern");
            %(fail)s;}

        if (%(x)s->dimensions[1] != %(y)s->dimensions[1]) {
            PyErr_SetString(PyExc_NotImplementedError,
              "x's number of columns doesn't match y's rows! Note: sampling_dot is different from dot because y is assumed to be transposed.");
            %(fail)s;}

        if (%(y)s->dimensions[0] != ((npy_int32 *)%(p_ncols)s->data)[0] ||
            %(x)s->dimensions[0] != (%(p_ptr)s->dimensions[0] - 1))
        {PyErr_SetString(PyExc_NotImplementedError,
        "The dimension of the pattern and the output must match"); %(fail)s;}

        // Allocate output
        if (!%(z_data)s
            || (%(z_data)s->dimensions[0] != %(p_data)s->dimensions[0])
            || (%(z_data)s->descr->type_num != %(typenum_zd)s)) {
            {Py_XDECREF(%(z_data)s);}
            npy_intp dims[] = {0};
            dims[0] = %(p_data)s->dimensions[0];
            %(z_data)s = (PyArrayObject*) PyArray_SimpleNew(1, dims,
                                                            %(typenum_zd)s);
        }
        if (!%(z_ind)s
            || (%(z_ind)s->dimensions[0] != %(p_ind)s->dimensions[0])
            || (%(z_ind)s->descr->type_num != %(typenum_zi)s)) {
            {Py_XDECREF(%(z_ind)s);}
            npy_intp dims[] = {0};
            dims[0] = %(p_ind)s->dimensions[0];
            %(z_ind)s = (PyArrayObject*) PyArray_SimpleNew(1, dims,
                                                           %(typenum_zi)s);
        }
        if (!%(z_ptr)s
            || (%(z_ptr)s->dimensions[0] != %(p_ptr)s->dimensions[0])
            || (%(z_ptr)s->descr->type_num != %(typenum_zp)s)) {
            {Py_XDECREF(%(z_ptr)s);}
            npy_intp dims[] = {0};
            dims[0] = %(p_ptr)s->dimensions[0];
            %(z_ptr)s = (PyArrayObject*) PyArray_SimpleNew(1, dims,
                                                           %(typenum_zp)s);
        }

        {
            // Product of MxK and NxK, output MxN
            npy_intp M = %(x)s->dimensions[0];
            npy_intp N = %(y)s->dimensions[0];
            npy_intp K = %(y)s->dimensions[1];

            // pointers to access actual data in the arrays passed as params.
            const dtype_%(x)s* __restrict__ Dx = (dtype_%(x)s*)%(x)s->data;
            const dtype_%(y)s* __restrict__ Dy = (dtype_%(y)s*)%(y)s->data;
            const dtype_%(p_data)s* __restrict__ Dpd = (dtype_%(p_data)s*)%(p_data)s->data;
            const dtype_%(p_ind)s* __restrict__ Dpi = (dtype_%(p_ind)s*)%(p_ind)s->data;
            const dtype_%(p_ptr)s* __restrict__ Dpp = (dtype_%(p_ptr)s*)%(p_ptr)s->data;
            dtype_%(z_data)s* __restrict__ Dzd = (dtype_%(z_data)s*)%(z_data)s->data;
            dtype_%(z_ind)s* __restrict__ Dzi = (dtype_%(z_ind)s*)%(z_ind)s->data;
            dtype_%(z_ptr)s* __restrict__ Dzp = (dtype_%(z_ptr)s*)%(z_ptr)s->data;

            const npy_intp Sdx = %(x)s->strides[1]/%(x)s->descr->elsize;
            const npy_intp Sdy = %(y)s->strides[1]/%(y)s->descr->elsize;
            const npy_intp Sdpd = %(p_data)s->strides[0] / %(p_data)s->descr->elsize;
            const npy_intp Sdpi = %(p_ind)s->strides[0] / %(p_ind)s->descr->elsize;
            const npy_intp Sdpp = %(p_ptr)s->strides[0] / %(p_ptr)s->descr->elsize;
            const npy_intp Sdzd = %(z_data)s->strides[0] / %(z_data)s->descr->elsize;
            const npy_intp Sdzi = %(z_ind)s->strides[0] / %(z_ind)s->descr->elsize;
            const npy_intp Sdzp = %(z_ptr)s->strides[0] / %(z_ptr)s->descr->elsize;

            memcpy(Dzi, Dpi, %(p_ind)s->dimensions[0]*sizeof(dtype_%(p_ind)s));
            memcpy(Dzp, Dpp, %(p_ptr)s->dimensions[0]*sizeof(dtype_%(p_ptr)s));

            for (npy_int32 m = 0; m < M; ++m) {
                for (npy_int32 n_idx = Dpp[m * Sdpp]; n_idx < Dpp[(m+1)*Sdpp]; ++n_idx) {
                    const npy_int32 n = Dpi[n_idx * Sdpi]; // row index of non-null value for column K

                    const dtype_%(x)s* x_row = (dtype_%(x)s*)(%(x)s->data + %(x)s->strides[0] * m);

                    const dtype_%(y)s* y_col = (dtype_%(y)s*)(%(y)s->data + %(y)s->strides[0] * n);

                    Dzd[n_idx * Sdzd] = Dpd[n_idx * Sdpd] * %(cdot)s((int*)&K, (const %(conv_type)s*)x_row, (int*)&Sdx, (const %(conv_type)s*)y_col, (int*)&Sdy);
                }
            }
        }
        """ % dict(locals(), **sub)

        return rval
sampling_dot_csr = SamplingDotCSR()


# register a specialization to replace StructuredDot -> StructuredDotCSx
@gof.local_optimizer([_structured_dot])
def local_structured_dot(node):
    if node.op == _structured_dot:
        a, b = node.inputs
        if a.type.format == 'csc':
            a_val, a_ind, a_ptr, a_shape = csm_properties(a)
            a_nsparse = a_shape[0]
            return [sd_csc(a_val, a_ind, a_ptr, a_nsparse, b)]
        if a.type.format == 'csr':
            a_val, a_ind, a_ptr, a_shape = csm_properties(a)
            return [sd_csr(a_val, a_ind, a_ptr, b)]
    return False

# Commented out because
# a) it is only slightly faster than scipy these days, and sometimes a little
# slower, and
# b) the resulting graphs make it very difficult for an op to do size checking
# on the matrices involved.  dimension mismatches are hard to detect sensibly.
#register_specialize(local_structured_dot)


def structured_dot_grad(sparse_A, dense_B, ga):
    if sparse_A.type.format in ('csc', 'csr'):

        if sparse_A.type.format == 'csc':
            sdgcsx = sdg_csc
            CSx = CSC
        else:
            sdgcsx = sdg_csr
            CSx = CSR

        g_A_data = sdgcsx(csm_indices(sparse_A), \
                          csm_indptr(sparse_A), dense_B, ga)
        return CSx(g_A_data, csm_indices(sparse_A), \
                                 csm_indptr(sparse_A), \
                                 csm_shape(sparse_A))
    else:
        raise NotImplementedError()


class StructuredDotGradCSC(gof.Op):
    # Op that produces the grad of StructuredDot.

    # :param a_indices: Matrix indicies
    # :param a_indptr: Matrix indptr
    # :param b: Right operand
    # :param g_ab: Accumulated gradient.

    # :return: The grad of `a`.`b` for `a` accumulated
    #          with g_ab.

    # :note:
    # - The grad implemented is structured.
    # - a_* are the corresponding properties of a sparse
    #   matrix in csc format.

    def __eq__(self, other):
        return (type(self) == type(other))

    def __hash__(self):
        return hash(type(self))

    def __str__(self):
        return self.__class__.__name__

    def make_node(self, a_indices, a_indptr, b, g_ab):
        return gof.Apply(self, [a_indices, a_indptr, b, g_ab],
                               [tensor.tensor(g_ab.dtype, (False,))])

    def perform(self, node, (a_indices, a_indptr, b, g_ab), (out,)):
        g_a_data = numpy.zeros(a_indices.shape, dtype=g_ab.dtype)
        for j in xrange(len(a_indptr) - 1):
            ind0 = a_indptr[j]
            ind1 = a_indptr[j + 1]
            for i_idx in xrange(ind0, ind1):
                i = a_indices[i_idx]
                # Depending on the type of g_ab and b (sparse or dense),
                # the following dot product can result in a scalar or
                # a (1, 1) sparse matrix.
                dot_val = numpy.dot(g_ab[i], b[j].T)
                if isinstance(dot_val, scipy.sparse.spmatrix):
                    dot_val = dot_val[0, 0]
                g_a_data[i_idx] = dot_val
        out[0] = g_a_data

    def c_code_cache_version(self):
        return (1,)

    def c_code(self, node, name, (_indices, _indptr, _d, _g), (_zout, ), sub):

        if node.inputs[2].type.dtype in ('complex64', 'complex128'):
            raise NotImplementedError('Complex types are not supported for b')
        if node.inputs[3].type.dtype in ('complex64', 'complex128'):
            raise NotImplementedError('Complex types are not supported for '
                                      'g_ab')

        return """
        if (%(_d)s->nd != 2) {PyErr_SetString(PyExc_NotImplementedError, "rank(d) != 2"); %(fail)s;}
        if (%(_g)s->nd != 2) {PyErr_SetString(PyExc_NotImplementedError, "rank(g) != 2"); %(fail)s;}
        if (%(_indices)s->nd != 1) {PyErr_SetString(PyExc_NotImplementedError, "rank(indices) != 1"); %(fail)s;}
        if (%(_indptr)s->nd != 1) {PyErr_SetString(PyExc_NotImplementedError, "rank(indptr) != 1"); %(fail)s;}

        if( %(_indices)s->descr->type_num != PyArray_INT32) {
        PyErr_SetString(PyExc_NotImplementedError, "C"); %(fail)s;}

        if( %(_indptr)s->descr->type_num != PyArray_INT32)
        {PyErr_SetString(PyExc_NotImplementedError, "D"); %(fail)s;}

        if( %(_d)s->dimensions[1] != %(_g)s->dimensions[1])
        {PyErr_SetString(PyExc_NotImplementedError, "d and g have different numbers of columns"); %(fail)s;}

        if (!%(_zout)s
            || (%(_zout)s->dimensions[0] != %(_indices)s->dimensions[0]))
        {
            Py_XDECREF(%(_zout)s);
            %(_zout)s = (PyArrayObject*) PyArray_SimpleNew(1, %(_indices)s->dimensions, %(_g)s->descr->type_num);
        }

        {   //makes it compile even though labels jump over variable definitions.
            npy_intp nnz = %(_indices)s->dimensions[0];
            npy_intp N =  %(_indptr)s->dimensions[0]-1; //TODO: error checking with this

            npy_intp Sindices = %(_indices)s->strides[0]/%(_indices)s->descr->elsize;
            npy_intp Sindptr = %(_indptr)s->strides[0]/%(_indptr)s->descr->elsize;

            const npy_intp Sd1 = %(_d)s->strides[1]/%(_d)s->descr->elsize;
            const npy_intp Sg1 = %(_g)s->strides[1]/%(_g)s->descr->elsize;

            const npy_intp K = %(_d)s->dimensions[1];

            const npy_int32 * __restrict__ indptr = (npy_int32 *)%(_indptr)s->data;
            const npy_int32 * __restrict__ indices = (npy_int32 *)%(_indices)s->data;

            // loop over columns
            for (npy_int32 j = 0; j < N; ++j)
            {
                // extract j-th row of dense matrix
                const dtype_%(_d)s* __restrict__ d_row = (dtype_%(_d)s*)(%(_d)s->data + %(_d)s->strides[0] * j);
                if(j >= %(_d)s->dimensions[0]) {PyErr_SetString(PyExc_NotImplementedError, "G"); %(fail)s;}

                // for each non-null value in the sparse column
                for (npy_int32 i_idx = indptr[j * Sindptr]; i_idx < indptr[(j+1) * Sindptr]; ++i_idx)
                {
                    // extract row index of non-null value
                    npy_int32 i = indices[i_idx * Sindices];

                    // extract corresponding row in gradient
                    const dtype_%(_g)s* __restrict__ g_row = (dtype_%(_g)s*)(%(_g)s->data + %(_g)s->strides[0] * i);
                    double ip = 0.0;

                    // make sure that row index is not bigger than actual number of rows
                    // Note: wouldn't the above operation fail if that were the case ?
                    //       when would this ever be true anyway ?
                    if (i >= %(_g)s->dimensions[0])
                    {PyErr_SetString(PyExc_NotImplementedError, "H"); %(fail)s;}

                    // perform dot product of dense and sparse rows
                    for(int k = 0; k < K; ++k)
                    {
                        ip += d_row[k * Sd1] * g_row[k*Sg1];
                    }

                    // write resulting gradient to sparse output
                    ((dtype_%(_zout)s* __restrict__)(%(_zout)s->data + i_idx * %(_zout)s->strides[0]))[0] = ip;
                }
            }
        }

        """ % dict(locals(), **sub)

    def infer_shape(self, node, shapes):
        return [shapes[0]]

sdg_csc = StructuredDotGradCSC()


class StructuredDotGradCSR(gof.Op):
    # Op that produces the grad of StructuredDot.

    # :param a_indices: Matrix indicies
    # :param a_indptr: Matrix indptr
    # :param b: Right operand
    # :param g_ab: Accumulated gradient.

    # :return: The grad of `a`.`b` for `a` accumulated
    #          with g_ab.

    # :note:
    # - The grad implemented is structured.
    # - a_* are the corresponding properties of a sparse
    #   matrix in csr format.

    def __eq__(self, other):
        return (type(self) == type(other))

    def __hash__(self):
        return hash(type(self))

    def __str__(self):
        return self.__class__.__name__

    def make_node(self, a_indices, a_indptr, b, g_ab):
        return gof.Apply(self, [a_indices, a_indptr, b, g_ab],
                         [tensor.tensor(b.dtype, (False,))])

    def perform(self, node, (a_indices, a_indptr, b, g_ab), (out,)):
        g_a_data = numpy.zeros(a_indices.shape, dtype=g_ab.dtype)
        for i in xrange(len(a_indptr) - 1):  # loop over rows
            ind0 = a_indptr[i]
            ind1 = a_indptr[i + 1]
            # loop over values in that row (columns)
            for j_idx in xrange(ind0, ind1):
                j = a_indices[j_idx]
                # grad is dot product of i-th row of gradient with j-th row of b
                # Depending on the type of g_ab and b (sparse or dense),
                # the following dot product can result in a scalar or
                # a (1, 1) sparse matrix.
                dot_val = numpy.dot(g_ab[i], b[j].T)
                if isinstance(dot_val, scipy.sparse.spmatrix):
                    dot_val = dot_val[0, 0]
                g_a_data[j_idx] = dot_val
        out[0] = g_a_data

    def c_code_cache_version(self):
        return (1,)

    def c_code(self, node, name, (_indices, _indptr, _d, _g), (_zout, ), sub):

        if node.inputs[2].type.dtype in ('complex64', 'complex128'):
            raise NotImplementedError('Complex types are not supported for b')
        if node.inputs[3].type.dtype in ('complex64', 'complex128'):
            raise NotImplementedError('Complex types are not supported for '
                                      'g_ab')

        return """
        if (%(_d)s->nd != 2) {PyErr_SetString(PyExc_NotImplementedError, "rank(d) != 2"); %(fail)s;}
        if (%(_g)s->nd != 2) {PyErr_SetString(PyExc_NotImplementedError, "rank(g) != 2"); %(fail)s;}
        if (%(_indices)s->nd != 1) {PyErr_SetString(PyExc_NotImplementedError, "rank(indices) != 1"); %(fail)s;}
        if (%(_indptr)s->nd != 1) {PyErr_SetString(PyExc_NotImplementedError, "rank(indptr) != 1"); %(fail)s;}

        if( %(_indices)s->descr->type_num != PyArray_INT32) {
        PyErr_SetString(PyExc_NotImplementedError, "C"); %(fail)s;}

        if( %(_indptr)s->descr->type_num != PyArray_INT32)
        {PyErr_SetString(PyExc_NotImplementedError, "D"); %(fail)s;}

        if( %(_d)s->dimensions[1] != %(_g)s->dimensions[1])
        {PyErr_SetString(PyExc_NotImplementedError, "d and g have different numbers of columns"); %(fail)s;}

        if (!%(_zout)s
            || (%(_zout)s->dimensions[0] != %(_indices)s->dimensions[0]))
        {
            Py_XDECREF(%(_zout)s);
            %(_zout)s = (PyArrayObject*) PyArray_SimpleNew(1, %(_indices)s->dimensions, %(_g)s->descr->type_num);
        }

        {   //makes it compile even though labels jump over variable definitions.
            npy_intp nnz = %(_indices)s->dimensions[0];
            // extract number of rows
            npy_intp N =  %(_indptr)s->dimensions[0]-1; //TODO: error checking with this

            npy_intp Sindices = %(_indices)s->strides[0]/%(_indices)s->descr->elsize;
            npy_intp Sindptr = %(_indptr)s->strides[0]/%(_indptr)s->descr->elsize;

            const npy_intp Sd1 = %(_d)s->strides[1]/%(_d)s->descr->elsize;
            const npy_intp Sg1 = %(_g)s->strides[1]/%(_g)s->descr->elsize;

            const npy_intp K = %(_d)s->dimensions[1];

            const npy_int32 * __restrict__ indptr = (npy_int32 *)%(_indptr)s->data;
            const npy_int32 * __restrict__ indices = (npy_int32 *)%(_indices)s->data;

            // loop over columns of sparse matrix
            for (npy_int32 i = 0; i < N; ++i)
            {
                // for each non-null value in the sparse row
                for (npy_int32 j_idx = indptr[i * Sindptr]; j_idx < indptr[(i+1) * Sindptr]; ++j_idx)
                {
                    // extract column index of non-null value
                    npy_int32 j = indices[j_idx * Sindices];

                    // extract j-th row of dense matrix
                    const dtype_%(_d)s* __restrict__ d_row = (dtype_%(_d)s*)(%(_d)s->data + %(_d)s->strides[0] * j);
                    if(j >= %(_d)s->dimensions[0]) {PyErr_SetString(PyExc_NotImplementedError, "G"); %(fail)s;}

                    // extract corresponding row in gradient
                    const dtype_%(_g)s* __restrict__ g_row = (dtype_%(_g)s*)(%(_g)s->data + %(_g)s->strides[0] * i);
                    double ip = 0.0;

                    // make sure that row index is not bigger than actual number of rows
                    // Note: wouldn't the above operation fail if that were the case ?
                    //       when would this ever be true anyway ?
                    if (i >= %(_g)s->dimensions[0])
                    {PyErr_SetString(PyExc_NotImplementedError, "H"); %(fail)s;}

                    // perform dot product of dense and sparse rows
                    for(int k = 0; k < K; ++k)
                    {
                        ip += d_row[k * Sd1] * g_row[k*Sg1];
                    }

                    // write resulting gradient to sparse output
                    ((dtype_%(_zout)s* __restrict__)(%(_zout)s->data + j_idx * %(_zout)s->strides[0]))[0] = ip;
                }
            }
        }

        """ % dict(locals(), **sub)

    def infer_shape(self, node, shapes):
        return [shapes[0]]

sdg_csr = StructuredDotGradCSR()


class Dot(gof.op.Op):
    """Operation for efficiently calculating the dot product when
    one or all operands is sparse. Supported format are CSC and CSR.
    The output of the operation is dense.

    :param x: Matrix variable.
    :param y: Matrix variable.

    :return: The dot product `x`.`y` in a dense format.

    :note:
    - The grad implemented is regular, i.e. not structured.
    - At least one of `x` or `y` must be a sparse matrix.
    """

    def __eq__(self, other):
        return type(self) == type(other)

    def __hash__(self):
        return hash(type(self))

    def __str__(self):
        return "Sparse" + self.__class__.__name__

    def infer_shape(self, node, shapes):
        xshp, yshp = shapes
        x, y = node.inputs
        if x.ndim == 2 and y.ndim == 2:
            return [(xshp[0], yshp[1])]
        if x.ndim == 1 and y.ndim == 2:
            return [(yshp[1],)]
        if x.ndim == 2 and y.ndim == 1:
            return [(xshp[0],)]
        if x.ndim == 1 and y.ndim == 1:
            return [()]
        raise NotImplementedError()

    def make_node(self, x, y):
        dtype_out = scalar.upcast(x.type.dtype, y.type.dtype)

        if not _is_sparse_variable(x) and not _is_sparse_variable(y):
            raise TypeError(x)

        return gof.Apply(self, [x, y], [tensor.tensor(dtype=dtype_out,
                         broadcastable=(False, False))])

    def perform(self, node, inputs, out):
        x, y = inputs
        out = out[0]
        x_is_sparse = _is_sparse(x)
        y_is_sparse = _is_sparse(y)

        if not x_is_sparse and not y_is_sparse:
            raise TypeError(x)

        rval = x * y

        if x_is_sparse and y_is_sparse:
            rval = rval.toarray()

        out[0] = rval

    def grad(self, (x, y), (gz,)):
        assert _is_sparse_variable(x) or _is_sparse_variable(y)
        rval = []

        if _is_dense_variable(y):
            rval.append(tensor.dot(gz, y.T))
        else:
            rval.append(dot(gz, y.T))
        if _is_dense_variable(x):
            rval.append(tensor.dot(x.T, gz))
        else:
            rval.append(dot(x.T, gz))

        return rval
_dot = Dot()


def dot(x, y):
    """Operation for efficiently calculating the dot product when
    one or all operands is sparse. Supported format are CSC and CSR.
    The output of the operation is dense.

    :param x: Matrix variable.
    :param y: Matrix variable.

    :return: The dot product `x`.`y` in a dense format.

    :note:
    - The grad implemented is regular, i.e. not structured.
    - At least one of `x` or `y` must be a sparse matrix.
    """

    if hasattr(x, 'getnnz'):
        x = as_sparse_variable(x)
    if hasattr(y, 'getnnz'):
        y = as_sparse_variable(y)

    x_is_sparse_variable = _is_sparse_variable(x)
    y_is_sparse_variable = _is_sparse_variable(y)

    if not x_is_sparse_variable and not y_is_sparse_variable:
        raise TypeError()

    return _dot(x, y)


class Usmm(gof.op.Op):
    """Performs the expression is `alpha` * `x` `y` + `z`.

    :param x: Matrix variable.
    :param y: Matrix variable.
    :param z: Dense matrix.
    :param alpha: A tensor scalar.

    :return: The dense matrix resulting from `alpha` * `x` `y` + `z`.

    :note:
    - The grad is not implemented for this op.
    - At least one of `x` or `y` must be a sparse matrix.
    """

    # We don't implement the infer_shape as it is
    # inserted by optimization only.

    def __eq__(self, other):
        return type(self) == type(other)

    def __hash__(self):
        return hash(type(self))

    def __str__(self):
        return 'Usmm{no_inplace}'

    def make_node(self, alpha, x, y, z):
        if not _is_sparse_variable(x) and not _is_sparse_variable(y):
            # If x and y are tensor, we don't want to use this class
            # We should use Dot22 and Gemm in that case.
            raise TypeError(x)

        dtype_out = scalar.upcast(alpha.type.dtype, x.type.dtype,
                                  y.type.dtype, z.type.dtype)
        alpha = tensor.as_tensor_variable(alpha)
        z = tensor.as_tensor_variable(z)

        assert z.ndim == 2
        assert alpha.type.broadcastable == (True,) * alpha.ndim
        if not _is_sparse_variable(x):
            x = tensor.as_tensor_variable(x)
            assert x.ndim == 2
        if not _is_sparse_variable(y):
            y = tensor.as_tensor_variable(y)
            assert y.ndim == 2

        return gof.Apply(self, [alpha, x, y, z],
                         [tensor.tensor(dtype=dtype_out,
                                        broadcastable=(False, False))])

    def perform(self, node, (alpha, x, y, z), (out, )):
        x_is_sparse = _is_sparse(x)
        y_is_sparse = _is_sparse(y)

        if not x_is_sparse and not y_is_sparse:
            raise TypeError(x)

        rval = x * y
        if isinstance(rval, scipy.sparse.spmatrix):
            rval = rval.toarray()
        if rval.dtype == alpha.dtype:
            rval *= alpha  # Faster because operation is inplace
        else:
            rval = rval * alpha
        if rval.dtype == z.dtype:
            rval += z   # Faster because operation is inplace
        else:
            rval = rval + z

        out[0] = rval
usmm = Usmm()


class UsmmCscDense(gof.Op):
    # Performs the expression is `alpha` * `x` `y` + `z`.

    # :param x: Matrix variable.
    # :param y: Matrix variable.
    # :param z: Dense matrix.
    # :param alpha: A tensor scalar.

    # :return: The dense matrix resulting from `alpha` * `x` `y` + `z`.

    # :note:
    # - The grad is not implemented for this op.
    # - Optimized version os Usmm when `x` is in csc format and
    #   `y` is dense.

    def __init__(self, inplace):
        self.inplace = inplace
        if inplace:
            self.destroy_map = {0: [6]}

    def __str__(self):
        if self.inplace:
            return 'UsmmCscDense{inplace}'
        else:
            return 'UsmmCscDense{no_inplace}'

    def __eq__(self, other):
        return (type(self) == type(other)) and self.inplace == other.inplace

    def __hash__(self):
        return hash(type(self)) ^ self.inplace

    def make_node(self, alpha, x_val, x_ind, x_ptr, x_nrows, y, z):
        alpha = tensor.as_tensor_variable(alpha)
        x_val = tensor.as_tensor_variable(x_val)
        x_ind = tensor.as_tensor_variable(x_ind)
        x_ptr = tensor.as_tensor_variable(x_ptr)
        x_nrows = tensor.as_tensor_variable(x_nrows)
        y = tensor.as_tensor_variable(y)
        z = tensor.as_tensor_variable(z)
        assert x_ind.dtype == 'int32'
        assert x_ptr.dtype == 'int32'
        assert x_nrows.dtype == 'int32'
        assert alpha.ndim == 2 and alpha.type.broadcastable == (True, True)
        assert x_val.ndim == 1
        assert y.ndim == 2
        assert z.ndim == 2

        dtype_out = scalar.upcast(alpha.type.dtype, x_val.type.dtype,
            y.type.dtype, z.type.dtype)

        if dtype_out not in ('float32', 'float64'):
            raise NotImplementedError('only float types are supported in '
                                      'operands')

        if self.inplace:
            assert z.type.dtype == dtype_out

        # axpy work only with the same dtype, so we should upcast the input
        if dtype_out != alpha.type.dtype:
            alpha = tensor.cast(alpha, dtype_out)
        if dtype_out != x_val.type.dtype:
            x_val = tensor.cast(x_val, dtype_out)
        if dtype_out != y.type.dtype:
            y = tensor.cast(y, dtype_out)
        if dtype_out != z.type.dtype:
            z = tensor.cast(z, dtype_out)

        r = gof.Apply(self, [alpha, x_val, x_ind, x_ptr, x_nrows, y, z],
                [tensor.tensor(dtype_out, (False, y.type.broadcastable[1]))])
        return r

    def c_support_code(self):
        return blas.blas_header_text()

    def c_libraries(self):
        return blas.ldflags()

    def c_compile_args(self):
        return blas.ldflags(libs=False, flags=True)

    def c_lib_dirs(self):
        return blas.ldflags(libs=False, libs_dir=True)

    def c_header_dirs(self):
        return blas.ldflags(libs=False, include_dir=True)

    def c_code(self, node, name, inputs, outputs, sub):
        alpha, x_val, x_ind, x_ptr, x_nrows, y, z = inputs
        zn = outputs[0]
        if node.inputs[1].type.dtype in ('complex64', 'complex128'):
            raise NotImplementedError('Complex types are not supported for '
                                      'x_val')
        if node.inputs[5].type.dtype in ('complex64', 'complex128'):
            raise NotImplementedError('Complex types are not supported for y')
        if node.inputs[6].type.dtype != node.outputs[0].type.dtype:
            raise NotImplementedError('z and output must have same type')

        if node.inputs[1].type.dtype == "float32":
            conv_type = "float"
            axpy = "saxpy_"
        else:
            conv_type = "double"
            axpy = "daxpy_"
        # retrieve dtype numbers
        typenum_alpha = node.inputs[0].type.dtype_specs()[-1]
        typenum_x_val = node.inputs[1].type.dtype_specs()[-1]
        typenum_y = node.inputs[5].type.dtype_specs()[-1]
        typenum_z = node.inputs[6].type.dtype_specs()[-1]
        typenum_zn = node.outputs[0].type.dtype_specs()[-1]

        inplace = int(self.inplace)

        rval = """
        if (%(x_val)s->nd != 1) {PyErr_SetString(PyExc_NotImplementedError, "rank(x_val) != 1"); %(fail)s;}
        if (%(x_ind)s->nd != 1) {PyErr_SetString(PyExc_NotImplementedError, "rank(x_ind) != 1"); %(fail)s;}
        if (%(x_ptr)s->nd != 1) {PyErr_SetString(PyExc_NotImplementedError, "rank(x_ptr) != 1"); %(fail)s;}
        if (%(x_nrows)s->nd != 0) {PyErr_SetString(PyExc_NotImplementedError, "rank(x_nrows) != 0"); %(fail)s;}
        if (%(y)s->nd != 2) {PyErr_SetString(PyExc_NotImplementedError, "rank(y) != 2"); %(fail)s;}

        if (%(x_val)s->descr->type_num != %(typenum_x_val)s) {
        PyErr_SetString(PyExc_NotImplementedError, "Invalid type for x_val"); %(fail)s;}

        if (%(y)s->descr->type_num != %(typenum_y)s) {
        PyErr_SetString(PyExc_NotImplementedError, "Invalid type for y"); %(fail)s;}

        if (%(z)s->descr->type_num != %(typenum_z)s) {
        PyErr_SetString(PyExc_NotImplementedError, "Invalid type for z"); %(fail)s;}

        if (%(alpha)s->descr->type_num != %(typenum_alpha)s) {
        PyErr_SetString(PyExc_NotImplementedError, "Invalid type for alpha"); %(fail)s;}

        if (%(x_ind)s->descr->type_num != PyArray_INT32) {
        PyErr_SetString(PyExc_NotImplementedError, "x_ind dtype not INT32"); %(fail)s;}

        if (%(x_ptr)s->descr->type_num != PyArray_INT32)
        {PyErr_SetString(PyExc_NotImplementedError, "x_ptr dtype not INT32"); %(fail)s;}

        if (%(x_nrows)s->descr->type_num != PyArray_INT32)
        {PyErr_SetString(PyExc_NotImplementedError, "x_nrows dtype not INT32"); %(fail)s;}

        if (%(x_val)s->dimensions[0] != %(x_ind)s->dimensions[0])
        {PyErr_SetString(PyExc_NotImplementedError, "x_val and x_ind have different lengths"); %(fail)s;}

        if (%(x_ptr)s->dimensions[0] != %(y)s->dimensions[0]+1)
        {PyErr_SetString(PyExc_NotImplementedError, "x's number of columns doesn't match y's rows"); %(fail)s;}

        if (%(z)s->dimensions[0] != ((npy_int32 *)%(x_nrows)s->data)[0] || %(z)s->dimensions[1] != %(y)s->dimensions[1])
        {PyErr_SetString(PyExc_NotImplementedError, "The dimension of the allocated output doesn't match the correct output size."); %(fail)s;}

        if (PyArray_SIZE(%(alpha)s) != 1)
        {PyErr_SetString(PyExc_NotImplementedError, "The number of element in alpha must be 1"); %(fail)s;}

        if (%(alpha)s->nd != 2)
        {PyErr_SetString(PyExc_NotImplementedError, "The number dimension of alpha must be 2"); %(fail)s;}

        if (%(x_val)s->nd != 1)
        {PyErr_SetString(PyExc_NotImplementedError, "The number dimension of x_val must be 1"); %(fail)s;}

        if (%(y)s->nd != 2)
        {PyErr_SetString(PyExc_NotImplementedError, "The number dimension of y must be 2"); %(fail)s;}

        if (%(z)s->nd != 2)
        {PyErr_SetString(PyExc_NotImplementedError, "The number dimension of z must be 2"); %(fail)s;}

        if (%(inplace)s)
        {
            if (%(typenum_zn)s != %(typenum_z)s) {
            PyErr_SetString(PyExc_NotImplementedError, "When inplace the output dtype must be the same as the input"); %(fail)s;}

            Py_XDECREF(%(zn)s);
            %(zn)s = %(z)s;
            Py_INCREF(%(zn)s);
        }
        else if (!%(zn)s
            || (%(zn)s->dimensions[0] != ((npy_int32 *)%(x_nrows)s->data)[0])
            || (%(zn)s->dimensions[1] != %(y)s->dimensions[1])
            )
        {
            {Py_XDECREF(%(zn)s);}
            npy_intp dims[] = {0, 0};
            dims[0] = ((npy_int32 *)%(x_nrows)s->data)[0];
            dims[1] = %(y)s->dimensions[1];
            %(zn)s = (PyArrayObject*) PyArray_SimpleNew(2, dims, %(typenum_zn)s);
        }

        {
            // sparse array has size MxK, dense KxN, output MxN
            npy_intp M = %(zn)s->dimensions[0];
            npy_intp N = %(zn)s->dimensions[1];
            npy_intp K = %(y)s->dimensions[0];

            // pointers to access actual data in the arrays passed as params.
            const dtype_%(x_val)s* __restrict__ Dval = (dtype_%(x_val)s*)%(x_val)s->data;
            const npy_int32 * __restrict__ Dind = (npy_int32*)%(x_ind)s->data;
            const npy_int32 * __restrict__ Dptr = (npy_int32*)%(x_ptr)s->data;
            const dtype_%(alpha)s alpha = ((dtype_%(alpha)s*)%(alpha)s->data)[0];

            npy_intp Sz = %(z)s->strides[1] / %(z)s->descr->elsize;
            npy_intp Szn = %(zn)s->strides[1] / %(zn)s->descr->elsize;
            npy_intp Sval = %(x_val)s->strides[0] / %(x_val)s->descr->elsize;
            npy_intp Sind = %(x_ind)s->strides[0] / %(x_ind)s->descr->elsize;
            npy_intp Sptr = %(x_ptr)s->strides[0] / %(x_ptr)s->descr->elsize;
            npy_intp Sy = %(y)s->strides[1] / %(y)s->descr->elsize;


            if (!(%(inplace)s))
            {
                if (PyArray_CopyInto(%(zn)s, %(z)s))
                {
                    Py_XDECREF(%(zn)s);
                    %(fail)s;
                }
            }

            for (npy_int32 k = 0; k < K; ++k)
            {
                for (npy_int32 m_idx = Dptr[k * Sptr]; m_idx < Dptr[(k+1)*Sptr]; ++m_idx)
                {
                    const npy_int32 m = Dind[m_idx * Sind]; // row index of non-null value for column K

                    const dtype_%(x_val)s Amk = alpha * Dval[m_idx * Sval]; // actual value at that location

                    dtype_%(y)s* y_row = (dtype_%(y)s*)(%(y)s->data + %(y)s->strides[0] * k);
                    // axpy expects pointer to the beginning of memory arrays,
                    // so when the stride is negative, we need to get the
                    // last element
                    if (Sy < 0)
                        y_row += (K - 1) * Sy;

                    dtype_%(zn)s* z_row = (dtype_%(zn)s*)(%(zn)s->data + %(zn)s->strides[0] * m);
                    if (Szn < 0)
                        z_row += (N - 1) * Szn;

                    %(axpy)s((int*)&N, (%(conv_type)s*)&Amk, (%(conv_type)s*)y_row, (int*)&Sy, (%(conv_type)s*)z_row, (int*)&Szn);
                }
            }
        }
        """ % dict(locals(), **sub)

        return rval

    def c_code_cache_version(self):
        return (1,)


usmm_csc_dense = UsmmCscDense(inplace=False)
usmm_csc_dense_inplace = UsmmCscDense(inplace=True)
