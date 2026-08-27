import numpy as np

frame = np.array([[120,56,54,130,20],[80,90,100,110,120]])
indices = np.indices(frame.shape)
yy = indices[0]
xx = indices[1]
print(indices)
print(indices.shape)
