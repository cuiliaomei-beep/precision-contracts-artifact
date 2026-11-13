// Stock Souffle embedding API. No transfer function or scheduling code changes.
#define __EMBEDDED_SOUFFLE__
#include "doop_snapshot.cpp"
#include <chrono>
#include <fstream>
#include <iomanip>
#include <memory>

int main(int argc, char** argv) {
    if (argc != 5) {
        std::cerr << "facts_dir output_dir workers timing_json\n";
        return 2;
    }
    using clock = std::chrono::steady_clock;
    double load = -1, compute = -1, output = -1;
    auto write = [&](const char* stage, bool complete) {
        std::ofstream f(argv[4]);
        f << std::setprecision(12)
          << "{\"stage\":\"" << stage << "\",\"complete\":" << (complete ? "true" : "false")
          << ",\"load_seconds\":" << load << ",\"compute_seconds\":" << compute
          << ",\"output_seconds\":" << output << "}\n";
    };
    try {
        std::unique_ptr<souffle::SouffleProgram> p(souffle::ProgramFactory::newInstance("doop_snapshot"));
        if (!p) throw std::runtime_error("compiled Souffle factory unavailable");
        p->setNumThreads(std::stoul(argv[3]));
        write("started", false);
        auto a = clock::now();
        p->loadAll(argv[1]);
        auto b = clock::now();
        load = std::chrono::duration<double>(b-a).count();
        write("loaded", false);
        p->run();
        auto c = clock::now();
        compute = std::chrono::duration<double>(c-b).count();
        write("computed", false);
        p->printAll(argv[2]);
        auto d = clock::now();
        output = std::chrono::duration<double>(d-c).count();
        write("complete", true);
        return 0;
    } catch (const std::exception& e) {
        write("failed", false);
        std::cerr << e.what() << "\n";
        return 2;
    }
}
