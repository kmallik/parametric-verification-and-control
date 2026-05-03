#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
EXAMPLES="$SCRIPT_DIR/examples/stable"
RUN="python $SCRIPT_DIR/src/param_synthesis.py"

$RUN "$EXAMPLES/LRW_1d_add_mathsat.json"
$RUN "$EXAMPLES/LRW_1d_add_mul_mathsat.json"
# $RUN "$EXAMPLES/LRW_1d_add_mul_z3.json"
# $RUN "$EXAMPLES/LRW_1d_add_z3.json"
$RUN "$EXAMPLES/LRW_1d_mul_mathsat.json"
# $RUN "$EXAMPLES/LRW_1d_mul_z3.json"
