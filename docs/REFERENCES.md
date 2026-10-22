# References

Each entry says where it is used in Quantivex.

## Methods used
1. **Variational quantum eigensolver.** A. Peruzzo et al., "A variational eigenvalue solver on a photonic quantum processor", *Nature Communications* 5, 4213 (2014). Used in `vqe`.
2. **Unitary coupled cluster.** J. Romero et al., "Strategies for quantum computing molecular energies using the unitary coupled cluster ansatz", *Quantum Science and Technology* 4, 014008 (2018). arXiv:1701.02691. The UCCSD ansatz in `chemistry.uccsd_excitations`.
3. **Jordan–Wigner transformation.** P. Jordan and E. Wigner, "Über das Paulische Äquivalenzverbot", *Zeitschrift für Physik* 47, 631 (1928). Used in `chemistry.qubit_hamiltonian`.
4. **PySCF.** Q. Sun et al., "Recent developments in the PySCF program package", *J. Chem. Phys.* 153, 024109 (2020). Supplies the RHF, CASCI and active-space integrals.
5. **Qiskit.** A. Javadi-Abhari et al., "Quantum computing with Qiskit", arXiv:2405.08810 (2024). Used for transpilation, the Aer simulator and the Target model.
6. **Noise-adaptive qubit mapping.** P. Murali et al., "Noise-Adaptive Compiler Mappings for Noisy Intermediate-Scale Quantum Computers", *ASPLOS* 2019. arXiv:1901.11054. The motivation for calibration-aware chain selection.
7. **Estimated success probability.** S. Nishio et al., "Extracting Success from IBM's 20-Qubit Machines Using Error-Aware Compilation", *ACM JETC* 16(3) (2020). arXiv:1903.10963. The ESP chain score in `hardware`.
8. **Calibration-scored layouts across a device (mapomatic).** P. D. Nation and M. Treinish, "Suppressing quantum circuit errors due to system variability", *PRX Quantum* 4, 010327 (2023). arXiv:2209.15512. The prior art Quantivex is compared against.
9. **Readout-error mitigation by expectation rescaling.** E. van den Berg, Z. Minev and K. Temme, "Model-free readout-error mitigation for quantum expectation values", *Phys. Rev. A* 105, 032620 (2022). arXiv:2012.09738. Quantivex uses the model-based form of the same rescaling (calibrated symmetric readout errors).
10. **Quantum Blur.** J. R. Wootton, "Procedural generation using quantum computation", *FDG/PCG Workshop* 2020. arXiv:2007.11510. And J. R. Wootton and M. Pfaffhauser, "Investigating the usefulness of Quantum Blur", arXiv:2112.01646 (2021). Implemented in `blur`.

## LLMs and quantum software (context for the agent design)
11. N. Dupuis et al., "Qiskit Code Assistant: Training LLMs for generating Quantum Computing Code", arXiv:2405.19495 (2024).
12. "QUASAR: Quantum Assembly Code Generation Using Tool-Augmented LLMs via Agentic RL", arXiv:2510.00967. Simulator-verified tool use, the pattern Quantivex follows: the LLM calls verified tools rather than writing circuits.
13. "A Protocol-Guided LLM Agent for Quantum Program Synthesis and Execution", arXiv:2609.20258. Constrained artifacts, validation and structured error repair.
14. "Programming Quantum Computers with Large Language Models", arXiv:2506.18125. Reports the limits of LLM-written circuits.

## Hackathon
15. K. M. Jablonka et al., "14 examples of how LLMs can transform materials science and chemistry", *Digital Discovery* (2023). doi:10.1039/D3DD00113J. Write-up of the first edition of this hackathon.

## Data
- `examples/ibm_torino_2025-02-26.json`: IBM `ibm_torino` calibration (last update 2025-02-26), as shipped in `qiskit-ibm-runtime` 0.50.0 (`FakeTorino`). The backend was retired around 1 April 2026.

## Statistics (confidence radius and correlation test)
- W. Hoeffding, "Probability inequalities for sums of bounded random variables", *J. Amer. Statist. Assoc.* 58, 13 (1963).
- A. Maurer and M. Pontil, "Empirical Bernstein bounds and sample variance penalization", *Proc. COLT* (2009); arXiv:0907.3740 (Theorem 4 is the bound used).
- P. Good, *Permutation, Parametric and Bootstrap Tests of Hypotheses*, 3rd ed. (Springer, 2005) (permutation test for Spearman's rho).

## Future work cited in the retrospective
- H. R. Grimsley et al., "An adaptive variational algorithm for exact molecular simulations on a quantum computer", *Nature Communications* 10, 3007 (2019) (ADAPT-VQE).
- Y. S. Yordanov, D. R. M. Arvidsson-Shukur and C. H. W. Barnes, "Efficient quantum circuits for quantum computational chemistry", *Phys. Rev. A* 102, 062612 (2020).
