from textwrap import indent
from typing import Optional, List, Tuple, Dict, Union, Any

import torch
import scipy.sparse

from torch_sparse.storage import SparseStorage, get_layout
from torch_sparse.utils import is_scalar


@torch.jit.script
class SparseTensor(object):
    storage: SparseStorage

    def __init__(self, row: Optional[torch.Tensor] = None,
                 rowptr: Optional[torch.Tensor] = None,
                 col: Optional[torch.Tensor] = None,
                 value: Optional[torch.Tensor] = None,
                 sparse_sizes: List[int] = None, is_sorted: bool = False):
        self.storage = SparseStorage(row=row, rowptr=rowptr, col=col,
                                     value=value, sparse_sizes=sparse_sizes,
                                     rowcount=None, colptr=None, colcount=None,
                                     csr2csc=None, csc2csr=None,
                                     is_sorted=is_sorted)

    @classmethod
    def from_storage(self, storage: SparseStorage):
        self = SparseTensor.__new__(SparseTensor)
        self.storage = storage
        return self

    @classmethod
    def from_dense(self, mat: torch.Tensor):
        if mat.dim() > 2:
            index = mat.abs().sum([i for i in range(2, mat.dim())]).nonzero()
        else:
            index = mat.nonzero()
        index = index.t()

        row, col = index[0], index[1]
        return SparseTensor(row=row, rowptr=None, col=col, value=mat[row, col],
                            sparse_sizes=mat.size()[:2], is_sorted=True)

    @classmethod
    def from_torch_sparse_coo_tensor(self, mat: torch.Tensor):
        mat = mat.coalesce()
        index = mat._indices()
        row, col = index[0], index[1]
        return SparseTensor(row=row, rowptr=None, col=col, value=mat._values(),
                            sparse_sizes=mat.size()[:2], is_sorted=True)

    @classmethod
    def eye(self, M: int, N: Optional[int] = None,
            options: Optional[torch.Tensor] = None, has_value: bool = True,
            fill_cache: bool = False):

        N = M if N is None else N

        if options is not None:
            row = torch.arange(min(M, N), device=options.device)
        else:
            row = torch.arange(min(M, N))
        col = row

        rowptr = torch.arange(M + 1, dtype=torch.long, device=row.device)
        if M > N:
            rowptr[N + 1:] = N

        value: Optional[torch.Tensor] = None
        if has_value:
            if options is not None:
                value = torch.ones(row.numel(), dtype=options.dtype,
                                   device=row.device)
            else:
                value = torch.ones(row.numel(), device=row.device)

        rowcount: Optional[torch.Tensor] = None
        colptr: Optional[torch.Tensor] = None
        colcount: Optional[torch.Tensor] = None
        csr2csc: Optional[torch.Tensor] = None
        csc2csr: Optional[torch.Tensor] = None

        if fill_cache:
            rowcount = torch.ones(M, dtype=torch.long, device=row.device)
            if M > N:
                rowcount[N:] = 0

            colptr = torch.arange(N + 1, dtype=torch.long, device=row.device)
            colcount = torch.ones(N, dtype=torch.long, device=row.device)
            if N > M:
                colptr[M + 1:] = M
                colcount[M:] = 0
            csr2csc = csc2csr = row

        storage: SparseStorage = SparseStorage(
            row=row, rowptr=rowptr, col=col, value=value,
            sparse_sizes=torch.Size([M, N]), rowcount=rowcount, colptr=colptr,
            colcount=colcount, csr2csc=csr2csc, csc2csr=csc2csr,
            is_sorted=True)

        self = SparseTensor.__new__(SparseTensor)
        self.storage = storage
        return self

    def copy(self):
        return self.from_storage(self.storage)

    def clone(self):
        return self.from_storage(self.storage.clone())

    def type_as(self, tensor=torch.Tensor):
        value = self.storage._value
        if value is None or tensor.dtype == value.dtype:
            return self
        return self.from_storage(self.storage.type_as(tensor))

    def device_as(self, tensor: torch.Tensor, non_blocking: bool = False):
        if tensor.device == self.device():
            return self
        return self.from_storage(self.storage.device_as(tensor, non_blocking))

    # Formats #################################################################

    def coo(self) -> Tuple[torch.Tensor, torch.Tensor, Optional[torch.Tensor]]:
        return self.storage.row(), self.storage.col(), self.storage.value()

    def csr(self) -> Tuple[torch.Tensor, torch.Tensor, Optional[torch.Tensor]]:
        return self.storage.rowptr(), self.storage.col(), self.storage.value()

    def csc(self) -> Tuple[torch.Tensor, torch.Tensor, Optional[torch.Tensor]]:
        perm = self.storage.csr2csc()
        value = self.storage.value()
        if value is not None:
            value = value[perm]
        return self.storage.colptr(), self.storage.row()[perm], value

    # Storage inheritance #####################################################

    def has_value(self) -> bool:
        return self.storage.has_value()

    def set_value_(self, value: Optional[torch.Tensor],
                   layout: Optional[str] = None):
        self.storage.set_value_(value, layout)
        return self

    def set_value(self, value: Optional[torch.Tensor],
                  layout: Optional[str] = None):
        return self.from_storage(self.storage.set_value(value, layout))

    def sparse_sizes(self) -> List[int]:
        return self.storage.sparse_sizes()

    def sparse_size(self, dim: int) -> int:
        return self.storage.sparse_sizes()[dim]

    def sparse_resize(self, sparse_sizes: List[int]):
        return self.from_storage(self.storage.sparse_resize(sparse_sizes))

    def is_coalesced(self) -> bool:
        return self.storage.is_coalesced()

    def coalesce(self, reduce: str = "add"):
        return self.from_storage(self.storage.coalesce(reduce))

    def fill_cache_(self):
        self.storage.fill_cache_()
        return self

    def clear_cache_(self):
        self.storage.clear_cache_()
        return self

    # Utility functions #######################################################

    def fill_value_(self, fill_value: float,
                    options: Optional[torch.Tensor] = None):
        if options is not None:
            value = torch.full((self.nnz(), ), fill_value, dtype=options.dtype,
                               device=self.device())
        else:
            value = torch.full((self.nnz(), ), fill_value,
                               device=self.device())
        return self.set_value_(value, layout='coo')

    def fill_value(self, fill_value: float,
                   options: Optional[torch.Tensor] = None):
        if options is not None:
            value = torch.full((self.nnz(), ), fill_value, dtype=options.dtype,
                               device=self.device())
        else:
            value = torch.full((self.nnz(), ), fill_value,
                               device=self.device())
        return self.set_value(value, layout='coo')

    def sizes(self) -> List[int]:
        sizes = self.sparse_sizes()
        value = self.storage.value()
        if value is not None:
            sizes = list(sizes) + list(value.size())[1:]
        return sizes

    def size(self, dim: int) -> int:
        return self.sizes()[dim]

    def dim(self) -> int:
        return len(self.sizes())

    def nnz(self) -> int:
        return self.storage.col().numel()

    def numel(self) -> int:
        value = self.storage.value()
        if value is not None:
            return value.numel()
        else:
            return self.nnz()

    def density(self) -> float:
        return self.nnz() / (self.sparse_size(0) * self.sparse_size(1))

    def sparsity(self) -> float:
        return 1 - self.density()

    def avg_row_length(self) -> float:
        return self.nnz() / self.sparse_size(0)

    def avg_col_length(self) -> float:
        return self.nnz() / self.sparse_size(1)

    def is_quadratic(self) -> bool:
        return self.sparse_size(0) == self.sparse_size(1)

    def is_symmetric(self) -> bool:
        if not self.is_quadratic():
            return False

        rowptr, col, value1 = self.csr()
        colptr, row, value2 = self.csc()

        if (rowptr != colptr).any() or (col != row).any():
            return False

        if value1 is None or value2 is None:
            return True
        else:
            return bool((value1 == value2).all())

    def detach_(self):
        value = self.storage.value()
        if value is not None:
            value.detach_()
        return self

    def detach(self):
        value = self.storage.value()
        if value is not None:
            value = value.detach()
        return self.set_value(value, layout='coo')

    def requires_grad(self) -> bool:
        value = self.storage.value()
        if value is not None:
            return value.requires_grad
        else:
            return False

    def requires_grad_(self, requires_grad: bool = True,
                       options: Optional[torch.Tensor] = None):
        if requires_grad and not self.has_value():
            self.fill_value_(1., options=options)

        value = self.storage.value()
        if value is not None:
            value.requires_grad_(requires_grad)
        return self

    def pin_memory(self):
        return self.from_storage(self.storage.pin_memory())

    def is_pinned(self) -> bool:
        return self.storage.is_pinned()

    def options(self) -> torch.Tensor:
        value = self.storage.value()
        if value is not None:
            return value
        else:
            return torch.tensor(0., device=self.storage.col().device)

    def device(self):
        return self.storage.col().device

    def cpu(self):
        return self.device_as(torch.tensor(0.), non_blocking=False)

    def cuda(self, options=Optional[torch.Tensor], non_blocking: bool = False):
        if options is not None:
            return self.device_as(options, non_blocking)
        else:
            options = torch.tensor(0.).cuda()
            return self.device_as(options, non_blocking)

    def is_cuda(self) -> bool:
        return self.storage.col().is_cuda

    def dtype(self):
        return self.options().dtype

    def is_floating_point(self) -> bool:
        return torch.is_floating_point(self.options())

    def bfloat16(self):
        return self.type_as(
            torch.tensor(0, dtype=torch.bfloat16, device=self.device()))

    def bool(self):
        return self.type_as(
            torch.tensor(0, dtype=torch.bool, device=self.device()))

    def byte(self):
        return self.type_as(
            torch.tensor(0, dtype=torch.uint8, device=self.device()))

    def char(self):
        return self.type_as(
            torch.tensor(0, dtype=torch.int8, device=self.device()))

    def half(self):
        return self.type_as(
            torch.tensor(0, dtype=torch.half, device=self.device()))

    def float(self):
        return self.type_as(
            torch.tensor(0, dtype=torch.float, device=self.device()))

    def double(self):
        return self.type_as(
            torch.tensor(0, dtype=torch.double, device=self.device()))

    def short(self):
        return self.type_as(
            torch.tensor(0, dtype=torch.short, device=self.device()))

    def int(self):
        return self.type_as(
            torch.tensor(0, dtype=torch.int, device=self.device()))

    def long(self):
        return self.type_as(
            torch.tensor(0, dtype=torch.long, device=self.device()))

    # Conversions #############################################################

    def to_dense(self, options: Optional[torch.Tensor] = None):
        row, col, value = self.coo()

        if value is not None:
            mat = torch.zeros(self.sizes(), dtype=value.dtype,
                              device=self.device())
        elif options is not None:
            mat = torch.zeros(self.sizes(), dtype=options.dtype,
                              device=self.device())
        else:
            mat = torch.zeros(self.sizes(), device=self.device())

        if value is not None:
            mat[row, col] = value
        else:
            mat[row, col] = torch.ones(self.nnz(), dtype=mat.dtype,
                                       device=mat.device)

        return mat

    def to_torch_sparse_coo_tensor(self, options: Optional[torch.Tensor]):
        row, col, value = self.coo()
        index = torch.stack([row, col], dim=0)
        if value is None:
            if options is not None:
                value = torch.ones(self.nnz(), dtype=options.dtype,
                                   device=self.device())
            else:
                value = torch.ones(self.nnz(), device=self.device())

        return torch.sparse_coo_tensor(index, value, self.sizes())

    # Standard Operators ######################################################

    # def __matmul__(self, other):
    #     return matmul(self, other, reduce='sum')


# SparseTensor.matmul = matmul

# Python Bindings #############################################################

Dtype = Optional[torch.dtype]
Device = Optional[Union[torch.device, str]]


@torch.jit.ignore
def share_memory_(self: SparseTensor) -> SparseTensor:
    self.storage.share_memory_()


@torch.jit.ignore
def is_shared(self: SparseTensor) -> bool:
    return self.storage.is_shared()


@torch.jit.ignore
def to(self, *args: Optional[List[Any]],
       **kwargs: Optional[Dict[str, Any]]) -> SparseTensor:

    dtype: Dtype = getattr(kwargs, 'dtype', None)
    device: Device = getattr(kwargs, 'device', None)
    non_blocking: bool = getattr(kwargs, 'non_blocking', False)

    for arg in args:
        if isinstance(arg, str) or isinstance(arg, torch.device):
            device = arg
        if isinstance(arg, torch.dtype):
            dtype = arg

    if dtype is not None:
        self = self.type_as(torch.tensor(0., dtype=dtype))
    if device is not None:
        self = self.device_as(torch.tensor(0., device=device), non_blocking)

    return self


@torch.jit.ignore
def __getitem__(self: SparseTensor, index: Any) -> SparseTensor:
    index = list(index) if isinstance(index, tuple) else [index]
    # More than one `Ellipsis` is not allowed...
    if len([i for i in index if not torch.is_tensor(i) and i == ...]) > 1:
        raise SyntaxError

    dim = 0
    out = self
    while len(index) > 0:
        item = index.pop(0)
        if isinstance(item, int):
            out = out.select(dim, item)
            dim += 1
        elif isinstance(item, slice):
            if item.step is not None:
                raise ValueError('Step parameter not yet supported.')

            start = 0 if item.start is None else item.start
            start = self.size(dim) + start if start < 0 else start

            stop = self.size(dim) if item.stop is None else item.stop
            stop = self.size(dim) + stop if stop < 0 else stop

            out = out.narrow(dim, start, max(stop - start, 0))
            dim += 1
        elif torch.is_tensor(item):
            if item.dtype == torch.bool:
                out = out.masked_select(dim, item)
                dim += 1
            elif item.dtype == torch.long:
                out = out.index_select(dim, item)
                dim += 1
        elif item == Ellipsis:
            if self.dim() - len(index) < dim:
                raise SyntaxError
            dim = self.dim() - len(index)
        else:
            raise SyntaxError

    return out


@torch.jit.ignore
def __repr__(self: SparseTensor) -> str:
    i = ' ' * 6
    row, col, value = self.coo()
    infos = []
    infos += [f'row={indent(row.__repr__(), i)[len(i):]}']
    infos += [f'col={indent(col.__repr__(), i)[len(i):]}']

    if value is not None:
        infos += [f'val={indent(value.__repr__(), i)[len(i):]}']

    infos += [
        f'size={tuple(self.sizes())}, '
        f'nnz={self.nnz()}, '
        f'density={100 * self.density():.02f}%'
    ]
    infos = ',\n'.join(infos)

    i = ' ' * (len(self.__class__.__name__) + 1)
    return f'{self.__class__.__name__}({indent(infos, i)[len(i):]})'


SparseTensor.share_memory_ = share_memory_
SparseTensor.is_shared = is_shared
SparseTensor.to = to
SparseTensor.__getitem__ = __getitem__
SparseTensor.__repr__ = __repr__

# Scipy Conversions ###########################################################

ScipySparseMatrix = Union[scipy.sparse.coo_matrix, scipy.sparse.
                          csr_matrix, scipy.sparse.csc_matrix]


@torch.jit.ignore
def from_scipy(mat: ScipySparseMatrix) -> SparseTensor:
    colptr = None
    if isinstance(mat, scipy.sparse.csc_matrix):
        colptr = torch.from_numpy(mat.indptr).to(torch.long)

    mat = mat.tocsr()
    rowptr = torch.from_numpy(mat.indptr).to(torch.long)
    mat = mat.tocoo()
    row = torch.from_numpy(mat.row).to(torch.long)
    col = torch.from_numpy(mat.col).to(torch.long)
    value = torch.from_numpy(mat.data)
    sparse_sizes = mat.shape[:2]

    storage = SparseStorage(row=row, rowptr=rowptr, col=col, value=value,
                            sparse_sizes=sparse_sizes, rowcount=None,
                            colptr=colptr, colcount=None, csr2csc=None,
                            csc2csr=None, is_sorted=True)

    return SparseTensor.from_storage(storage)


@torch.jit.ignore
def to_scipy(self: SparseTensor, layout: Optional[str] = None,
             dtype: Optional[torch.dtype] = None) -> ScipySparseMatrix:
    assert self.dim() == 2
    layout = get_layout(layout)

    if not self.has_value():
        ones = torch.ones(self.nnz(), dtype=dtype).numpy()

    if layout == 'coo':
        row, col, value = self.coo()
        row = row.detach().cpu().numpy()
        col = col.detach().cpu().numpy()
        value = value.detach().cpu().numpy() if self.has_value() else ones
        return scipy.sparse.coo_matrix((value, (row, col)), self.sizes())
    elif layout == 'csr':
        rowptr, col, value = self.csr()
        rowptr = rowptr.detach().cpu().numpy()
        col = col.detach().cpu().numpy()
        value = value.detach().cpu().numpy() if self.has_value() else ones
        return scipy.sparse.csr_matrix((value, col, rowptr), self.sizes())
    elif layout == 'csc':
        colptr, row, value = self.csc()
        colptr = colptr.detach().cpu().numpy()
        row = row.detach().cpu().numpy()
        value = value.detach().cpu().numpy() if self.has_value() else ones
        return scipy.sparse.csc_matrix((value, row, colptr), self.sizes())


SparseTensor.from_scipy = from_scipy
SparseTensor.to_scipy = to_scipy

# Hacky fixes #################################################################

# Fix standard operators of `torch.Tensor` for PyTorch<=1.3.
# https://github.com/pytorch/pytorch/pull/31769
TORCH_MAJOR = int(torch.__version__.split('.')[0])
TORCH_MINOR = int(torch.__version__.split('.')[1])
if (TORCH_MAJOR < 1) or (TORCH_MAJOR == 1 and TORCH_MINOR < 4):

    def add(self, other):
        if torch.is_tensor(other) or is_scalar(other):
            return self.add(other)
        return NotImplemented

    def mul(self, other):
        if torch.is_tensor(other) or is_scalar(other):
            return self.mul(other)
        return NotImplemented

    torch.Tensor.__add__ = add
    torch.Tensor.__mul__ = mul
