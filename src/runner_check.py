from system import benchmark_runner

if __name__ == "__main__":
    # config_file = "src/benchmark/random_walk_verification_0.json"
    # config_file = "src/benchmark/test_out_reach_avoid.json"
    config_file = "src/benchmark/test_out_reach_avoid.json"

    benchmark_runner(config_file, iterations=1)
