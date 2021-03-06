import numpy as np
import numpy

import theano
from theano.tests import unittest_tools as utt
from theano.tensor.extra_ops import *
from theano import tensor as T
from theano import config, tensor, function, scalar


class TestBinCountOp(utt.InferShapeTester):
    def setUp(self):
        super(TestBinCountOp, self).setUp()
        self.op_class = BinCountOp
        self.op = BinCountOp()

    def test_bincountOp(self):
        x = T.lvector('x')
        w = T.vector('w')
        a = np.random.random_integers(50, size=(25))
        weights = np.random.random((25,)).astype(config.floatX)

        f1 = theano.function([x], bincount(x))
        f2 = theano.function([x, w], bincount(x, weights=w))
        f3 = theano.function([x], bincount(x, minlength=23))
        f4 = theano.function([x], bincount(x, minlength=5))

        assert (np.bincount(a) == f1(a)).all()
        assert np.allclose(np.bincount(a, weights=weights), f2(a, weights))
        assert (np.bincount(a, minlength=23) == f3(a)).all()
        assert (np.bincount(a, minlength=5) == f3(a)).all()

    def test_infer_shape(self):
        x = T.lvector('x')

        self._compile_and_check([x],
                                [bincount(x)],
                                [np.random.random_integers(50, size=(25,))],
                                self.op_class)

        weights = np.random.random((25,)).astype(config.floatX)
        self._compile_and_check([x],
                                [bincount(x, weights=weights)],
                                [np.random.random_integers(50, size=(25,))],
                                self.op_class)

        self._compile_and_check([x],
                                [bincount(x, minlength=60)],
                                [np.random.random_integers(50, size=(25,))],
                                self.op_class)

        self._compile_and_check([x],
                                [bincount(x, minlength=5)],
                                [np.random.random_integers(50, size=(25,))],
                                self.op_class)


class TestDiffOp(utt.InferShapeTester):
    nb = 10  # Number of time iterating for n

    def setUp(self):
        super(TestDiffOp, self).setUp()
        self.op_class = DiffOp
        self.op = DiffOp()

    def test_diffOp(self):
        x = T.matrix('x')
        a = np.random.random((30, 50)).astype(config.floatX)

        f = theano.function([x], diff(x))
        assert np.allclose(np.diff(a), f(a))

        for axis in range(len(a.shape)):
            for k in range(TestDiffOp.nb):
                g = theano.function([x], diff(x, n=k, axis=axis))
                assert np.allclose(np.diff(a, n=k, axis=axis), g(a))

    def test_infer_shape(self):
        x = T.matrix('x')
        a = np.random.random((30, 50)).astype(config.floatX)

        self._compile_and_check([x],
                                [self.op(x)],
                                [a],
                                self.op_class)

        for axis in range(len(a.shape)):
            for k in range(TestDiffOp.nb):
                self._compile_and_check([x],
                                        [diff(x, n=k, axis=axis)],
                                        [a],
                                        self.op_class)

    def test_grad(self):
        x = T.vector('x')
        a = np.random.random(50).astype(config.floatX)

        gf = theano.function([x], T.grad(T.sum(diff(x)), x))
        utt.verify_grad(self.op, [a])

        for k in range(TestDiffOp.nb):
            dg = theano.function([x], T.grad(T.sum(diff(x, n=k)), x))
            utt.verify_grad(DiffOp(n=k), [a], eps=7e-3)


class TestSqueezeOp(utt.InferShapeTester):
    def setUp(self):
        super(TestSqueezeOp, self).setUp()
        self.op_class = SqueezeOp
        self.op = SqueezeOp(out_nd=1)

    def test_squeezeOp(self):
        x = T.matrix('x')
        a = np.random.random((1, 50)).astype(config.floatX)

        f = theano.function([x], squeeze(x, out_nd=1))
        assert np.allclose(np.squeeze(a), f(a))

        x = T.tensor4('x')
        f = theano.function([x], squeeze(x, out_nd=2))

        a = np.random.random((1, 1, 2, 3)).astype(config.floatX)
        assert np.allclose(np.squeeze(a), f(a))

        a = np.random.random((1, 2, 2, 1)).astype(config.floatX)
        assert np.allclose(np.squeeze(a), f(a))

        a = np.random.random((4, 1, 2, 1)).astype(config.floatX)
        assert np.allclose(np.squeeze(a), f(a))

    def test_grad(self):
        x = T.tensor4('x')
        a = np.random.random((1, 1, 3, 4)).astype(config.floatX)

        gf = theano.function([x], T.grad(T.sum(squeeze(x, out_nd=1)), x))
        utt.verify_grad(SqueezeOp(out_nd=2), [a])


class TestRepeatOp(utt.InferShapeTester):
    def _possible_axis(self, ndim):
        return [None] + range(ndim) + [-i for i in range(ndim)]

    def setUp(self):
        super(TestRepeatOp, self).setUp()
        self.op_class = RepeatOp
        self.op = RepeatOp()

    def test_repeatOp(self):
        for ndim in range(3):
            x = T.TensorType(config.floatX, [False] * ndim)()
            a = np.random.random((10, ) * ndim).astype(config.floatX)

            for axis in self._possible_axis(ndim):
                r_var = T.lscalar()
                r = 3
                f = theano.function([x, r_var], repeat(x, r_var, axis=axis))
                assert np.allclose(np.repeat(a, r, axis=axis), f(a, r))

                r_var = T.lvector()
                if axis is None:
                    r = np.random.random_integers(5, size=a.size)
                else:
                    r = np.random.random_integers(5, size=(10,))

                f = theano.function([x, r_var], repeat(x, r_var, axis=axis))
                assert np.allclose(np.repeat(a, r, axis=axis), f(a, r))

    def test_infer_shape(self):
        for ndim in range(4):
            x = T.TensorType(config.floatX, [False] * ndim)()
            a = np.random.random((10, ) * ndim).astype(config.floatX)

            for axis in self._possible_axis(ndim):
                r_var = T.lscalar()
                r = 3
                self._compile_and_check([x, r_var],
                                        [RepeatOp(axis=axis)(x, r_var)],
                                        [a, r],
                                        self.op_class)

                r_var = T.lvector()
                if axis is None:
                    r = np.random.random_integers(5, size=a.size)
                else:
                    r = np.random.random_integers(5, size=(10,))

                self._compile_and_check([x, r_var],
                                        [RepeatOp(axis=axis)(x, r_var)],
                                        [a, r],
                                        self.op_class)

    def test_grad(self):
        for ndim in range(3):
            a = np.random.random((10, ) * ndim).astype(config.floatX)

            for axis in self._possible_axis(ndim):
                utt.verify_grad(lambda x: RepeatOp(axis=axis)(x, 3), [a])


class TestBartlett(utt.InferShapeTester):

    def setUp(self):
        super(TestBartlett, self).setUp()
        self.op_class = Bartlett
        self.op = bartlett

    def test_perform(self):
        x = tensor.lscalar()
        f = function([x], self.op(x))
        M = numpy.random.random_integers(3, 50, size=())
        assert numpy.allclose(f(M), numpy.bartlett(M))
        assert numpy.allclose(f(0), numpy.bartlett(0))
        assert numpy.allclose(f(-1), numpy.bartlett(-1))
        b = numpy.array([17], dtype='uint8')
        assert numpy.allclose(f(b[0]), numpy.bartlett(b[0]))

    def test_infer_shape(self):
        x = tensor.lscalar()
        self._compile_and_check([x], [self.op(x)],
                                [numpy.random.random_integers(3, 50, size=())],
                                self.op_class)
        self._compile_and_check([x], [self.op(x)], [0], self.op_class)
        self._compile_and_check([x], [self.op(x)], [1], self.op_class)


if __name__ == "__main__":
    t = TestBartlett('setUp')
    t.setUp()
    t.test_perform()
    t.test_infer_shape()


class TestFillDiagonal(utt.InferShapeTester):

    rng = numpy.random.RandomState(43)

    def setUp(self):
        super(TestFillDiagonal, self).setUp()
        self.op_class = FillDiagonal
        self.op = fill_diagonal

    def test_perform(self):
        x = tensor.matrix()
        y = tensor.scalar()
        f = function([x, y], fill_diagonal(x, y))
        for shp in [(8, 8), (5, 8), (8, 5)]:
            a = numpy.random.rand(*shp).astype(config.floatX)
            val = numpy.cast[config.floatX](numpy.random.rand())
            out = f(a, val)
            # We can't use numpy.fill_diagonal as it is bugged.
            assert numpy.allclose(numpy.diag(out), val)
            assert (out == val).sum() == min(a.shape)

        # test for 3d tensor
        a = numpy.random.rand(3, 3, 3).astype(config.floatX)
        x = tensor.tensor3()
        y = tensor.scalar()
        f = function([x, y], fill_diagonal(x, y))
        val = numpy.cast[config.floatX](numpy.random.rand() + 10)
        out = f(a, val)
        # We can't use numpy.fill_diagonal as it is bugged.
        assert out[0, 0, 0] == val
        assert out[1, 1, 1] == val
        assert out[2, 2, 2] == val
        assert (out == val).sum() == min(a.shape)

    def test_gradient(self):
        utt.verify_grad(fill_diagonal, [numpy.random.rand(5, 8),
                                        numpy.random.rand()],
                        n_tests=1, rng=TestFillDiagonal.rng)
        utt.verify_grad(fill_diagonal, [numpy.random.rand(8, 5),
                                        numpy.random.rand()],
                        n_tests=1, rng=TestFillDiagonal.rng)

    def test_infer_shape(self):
        z = tensor.dtensor3()
        x = tensor.dmatrix()
        y = tensor.dscalar()
        self._compile_and_check([x, y], [self.op(x, y)],
                                [numpy.random.rand(8, 5),
                                 numpy.random.rand()],
                                self.op_class)
        self._compile_and_check([z, y], [self.op(z, y)],
                                [numpy.random.rand(8, 8, 8),
                                 numpy.random.rand()],
                                self.op_class)

if __name__ == "__main__":
    utt.unittest.main()
    t = TestFillDiagonal('setUp')
    t.setUp()
    t.test_perform()
    t.test_gradient()
    t.test_infer_shape()
