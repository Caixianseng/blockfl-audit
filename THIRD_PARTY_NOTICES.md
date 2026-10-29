# Sources and third-party components

- Flower 1.19.0, Apache-2.0: https://github.com/flwrlabs/flower/tree/v1.19.0 . Uses its public NumPyClient and FedAvg APIs. PyTorch CIFAR-10 Quickstart is the architectural reference: https://flower.ai/docs/framework/tutorial-quickstart-pytorch.html .
- Besu 26.10.2, Apache-2.0: https://github.com/besu-eth/besu/releases/tag/26.10.2 . Four-validator genesis and QBFT configuration follow https://docs.besu-eth.org/private-networks/tutorials/qbft . Deployment here uses native processes rather than Quickstart Docker Compose because this host lacks a ready Docker/WSL runtime.
- Eclipse Temurin JDK 25, GPLv2 with Classpath Exception; the downloaded runtime retains its original legal files. Download URL and SHA-256 are recorded in network/runtime.json.
- OpenZeppelin Contracts 5.4.0, MIT: https://github.com/OpenZeppelin/openzeppelin-contracts/tree/v5.4.0 . AccessControl is imported in FLAudit.sol; npm locks record exact dependencies.
- PyTorch and Torchvision, BSD-style: https://github.com/pytorch/pytorch and https://github.com/pytorch/vision . Installed package versions are recorded in requirements.lock.
- Web3.py, MIT; Streamlit, Apache-2.0; Matplotlib, Matplotlib license; psutil, BSD-3-Clause. Original license files remain with installed distributions.
- CIFAR-10: Alex Krizhevsky, Learning Multiple Layers of Features from Tiny Images, 2009. Official source: https://www.cs.toronto.edu/~kriz/cifar.html . Dataset is downloaded separately and is not included in the source archive.

R3T was reviewed as a design reference; no R3T source code was copied. External OpenZeppelin agent skills were inspected during planning but not installed or copied. Locally available resource, experimental-design and visualization skills supported development workflows.
