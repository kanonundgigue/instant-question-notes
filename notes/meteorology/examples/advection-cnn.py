"""一次元移流方程式の時間発展を1次元CNNに学習させる。

解析解から教師データを作り、現在の気温偏差と風速から
1ステップ後の気温偏差を予測する。学習後はCNNの出力を
繰り返し入力し、解析解および一次精度風上差分法と比較する。
"""

from copy import deepcopy
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset


# ============================================================
# 1. 計算条件
# ============================================================

SEED = 42

# 0 km以上10 km未満を128点に分割する。
DOMAIN_LENGTH = 10.0  # 計算領域の長さ [km]
N_GRID = 128
DX = DOMAIN_LENGTH / N_GRID  # 格子間隔 [km]

# CNNが1回で予測する時間間隔。
DT = 0.05  # [h]

# 教師データの本数と長さ。
N_TRAIN_TRAJECTORIES = 240
N_VALID_TRAJECTORIES = 60
STEPS_PER_TRAJECTORY = 30

# 教師データに含める風速範囲。
MIN_WIND_SPEED = -1.0  # [km h^-1]
MAX_WIND_SPEED = 1.0  # [km h^-1]
WIND_SPEED_SCALE = 1.0  # 風速を無次元化する代表値 [km h^-1]
TEMPERATURE_SCALE = 10.0  # 気温偏差を無次元化する代表値 [K]

# 学習条件。
BATCH_SIZE = 128
MAX_EPOCHS = 120
LEARNING_RATE = 1.0e-3
PATIENCE = 15

# 未知データに対する反復予測の条件。
N_TEST_STEPS = 160
TEST_WIND_SPEED = 0.75  # [km h^-1]

# このファイルは notes/meteorology/examples/ に置くため、parents[3] が
# リポジトリルートになる。
ROOT = Path(__file__).resolve().parents[3]
FIG_DIR = ROOT / "images"


# ============================================================
# 2. 空間格子と初期気温分布
# ============================================================


def make_grid() -> np.ndarray:
    """周期境界を持つ等間隔格子を作る。"""
    # endpoint=False により、同じ場所を表す0 kmと10 kmの重複を避ける。
    return np.linspace(0.0, DOMAIN_LENGTH, N_GRID, endpoint=False)


def make_random_temperature(
    x: np.ndarray,
    rng: np.random.Generator,
) -> np.ndarray:
    """波数、振幅、位相が異なる波を重ねて初期分布を作る。"""
    temperature = np.zeros_like(x)

    # 1種類の波だけを使うと、CNNがその波形だけを記憶しやすい。
    # そこで波数1から6までの波をランダムに重ねる。
    for wave_number in range(1, 7):
        amplitude = rng.uniform(-3.0, 3.0) / np.sqrt(wave_number)
        phase = rng.uniform(0.0, 2.0 * np.pi)
        temperature += amplitude * np.sin(
            2.0 * np.pi * wave_number * x / DOMAIN_LENGTH + phase
        )

    # 平均気温ではなく平均からの偏差を学習するため、空間平均を0にする。
    return temperature - temperature.mean()


# ============================================================
# 3. 解析解による平行移動
# ============================================================


def exact_advection(
    temperature: np.ndarray,
    wind_speed: float,
    elapsed_time: float,
) -> np.ndarray:
    """周期領域上で気温分布を風下へ平行移動する。

    一定風速の移流方程式の解 T(x,t)=T(x-ut,0) を、
    フーリエ変換のシフト則で計算する。
    """
    # fftfreqの出力は cycle/kmなので、2πを掛けて rad/kmにする。
    wave_numbers = 2.0 * np.pi * np.fft.fftfreq(N_GRID, d=DX)
    spectrum = np.fft.fft(temperature)

    # 正の風速なら分布がx軸の正方向へ移動する。
    phase_shift = np.exp(
        -1j * wave_numbers * wind_speed * elapsed_time
    )

    # 丸め誤差による微小な虚部を.realで除く。
    return np.fft.ifft(spectrum * phase_shift).real


# ============================================================
# 4. 教師データ
# ============================================================


def generate_dataset(
    number_of_trajectories: int,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """現在場と風速を入力、1ステップ後の場を正解として作る。

    Returns
    -------
    inputs : np.ndarray
        shapeは (サンプル数, 2, N_GRID)。2チャンネルは気温と風速。
    targets : np.ndarray
        shapeは (サンプル数, 1, N_GRID)。1ステップ後の気温。
    """
    x = make_grid()
    input_samples: list[np.ndarray] = []
    target_samples: list[np.ndarray] = []

    for trajectory_index in range(number_of_trajectories):
        current_temperature = make_random_temperature(x, rng)
        wind_speed = rng.uniform(MIN_WIND_SPEED, MAX_WIND_SPEED)

        for _ in range(STEPS_PER_TRAJECTORY):
            next_temperature = exact_advection(
                current_temperature,
                wind_speed,
                DT,
            )

            # 気温偏差と風速を、およそ1の大きさへ無次元化する。
            scaled_temperature = current_temperature / TEMPERATURE_SCALE
            scaled_wind = np.full(
                N_GRID,
                wind_speed / WIND_SPEED_SCALE,
            )

            # CNNの入力shapeは (チャンネル数, 格子点数)。
            input_samples.append(
                np.stack([scaled_temperature, scaled_wind], axis=0)
            )
            target_samples.append(
                (next_temperature / TEMPERATURE_SCALE)[np.newaxis, :]
            )

            # 次の組では、1ステップ進んだ場を現在場として使う。
            current_temperature = next_temperature

        if (trajectory_index + 1) % 40 == 0:
            print(
                "Generated trajectories: "
                f"{trajectory_index + 1}/{number_of_trajectories}"
            )

    return (
        np.asarray(input_samples, dtype=np.float32),
        np.asarray(target_samples, dtype=np.float32),
    )


def make_loader(
    inputs: np.ndarray,
    targets: np.ndarray,
    shuffle: bool,
) -> DataLoader:
    """NumPy配列をPyTorchのミニバッチへ変換する。"""
    dataset = TensorDataset(
        torch.from_numpy(inputs),
        torch.from_numpy(targets),
    )
    return DataLoader(dataset, batch_size=BATCH_SIZE, shuffle=shuffle)


# ============================================================
# 5. 1次元CNN
# ============================================================


class AdvectionCNN(nn.Module):
    """現在の気温と風速から1ステップ後の気温を予測する。"""

    def __init__(self) -> None:
        super().__init__()
        self.network = nn.Sequential(
            # circular paddingにより、領域の左端と右端を接続する。
            nn.Conv1d(2, 32, kernel_size=5, padding=2, padding_mode="circular"),
            nn.Tanh(),
            nn.Conv1d(32, 32, kernel_size=5, padding=2, padding_mode="circular"),
            nn.Tanh(),
            nn.Conv1d(32, 1, kernel_size=5, padding=2, padding_mode="circular"),
        )

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        """現在場にCNNが求めた1ステップの補正量を足す。"""
        current_temperature = inputs[:, 0:1, :]
        correction = self.network(inputs)
        return current_temperature + correction


# ============================================================
# 6. 学習
# ============================================================


def calculate_loss(
    model: nn.Module,
    loader: DataLoader,
    loss_function: nn.Module,
    device: torch.device,
) -> float:
    """データ全体の平均損失を計算する。"""
    model.eval()
    loss_sum = 0.0
    sample_count = 0

    with torch.no_grad():
        for inputs, targets in loader:
            inputs = inputs.to(device)
            targets = targets.to(device)
            loss = loss_function(model(inputs), targets)
            loss_sum += loss.item() * inputs.shape[0]
            sample_count += inputs.shape[0]

    return loss_sum / sample_count


def train_model(
    model: nn.Module,
    train_loader: DataLoader,
    valid_loader: DataLoader,
    device: torch.device,
) -> tuple[list[float], list[float]]:
    """検証誤差を監視しながらCNNを学習する。"""
    loss_function = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)
    best_valid_loss = np.inf
    best_parameters = deepcopy(model.state_dict())
    epochs_without_improvement = 0
    train_history: list[float] = []
    valid_history: list[float] = []

    for epoch in range(1, MAX_EPOCHS + 1):
        model.train()
        loss_sum = 0.0
        sample_count = 0

        for inputs, targets in train_loader:
            inputs = inputs.to(device)
            targets = targets.to(device)

            optimizer.zero_grad()
            predictions = model(inputs)
            loss = loss_function(predictions, targets)
            loss.backward()
            optimizer.step()

            loss_sum += loss.item() * inputs.shape[0]
            sample_count += inputs.shape[0]

        train_loss = loss_sum / sample_count
        valid_loss = calculate_loss(
            model, valid_loader, loss_function, device
        )
        train_history.append(train_loss)
        valid_history.append(valid_loss)

        if epoch == 1 or epoch % 10 == 0:
            print(
                f"Epoch {epoch:03d}: "
                f"train={train_loss:.3e}, valid={valid_loss:.3e}"
            )

        if valid_loss < best_valid_loss:
            best_valid_loss = valid_loss
            best_parameters = deepcopy(model.state_dict())
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1

        if epochs_without_improvement >= PATIENCE:
            print(f"Early stopping at epoch {epoch}")
            break

    # 最後の重みではなく、検証誤差が最小だった重みを採用する。
    model.load_state_dict(best_parameters)
    print(f"Best validation loss: {best_valid_loss:.3e}")
    return train_history, valid_history


# ============================================================
# 7. CNNと風上差分法による反復予測
# ============================================================


def predict_with_cnn(
    model: nn.Module,
    initial_temperature: np.ndarray,
    wind_speed: float,
    number_of_steps: int,
    device: torch.device,
) -> np.ndarray:
    """CNNの出力を次の入力へ戻して長期予測を作る。"""
    predictions = np.zeros((number_of_steps + 1, N_GRID))
    predictions[0] = initial_temperature
    scaled_wind = np.full(
        N_GRID,
        wind_speed / WIND_SPEED_SCALE,
        dtype=np.float32,
    )

    model.eval()
    with torch.no_grad():
        for step in range(number_of_steps):
            scaled_temperature = (
                predictions[step] / TEMPERATURE_SCALE
            ).astype(np.float32)
            input_array = np.stack(
                [scaled_temperature, scaled_wind], axis=0
            )[np.newaxis, :, :]
            input_tensor = torch.from_numpy(input_array).to(device)
            scaled_next = model(input_tensor).cpu().numpy()[0, 0]
            predictions[step + 1] = scaled_next * TEMPERATURE_SCALE

    return predictions


def predict_with_upwind(
    initial_temperature: np.ndarray,
    wind_speed: float,
    number_of_steps: int,
) -> np.ndarray:
    """一次精度風上差分法で長期予測を作る。"""
    predictions = np.zeros((number_of_steps + 1, N_GRID))
    predictions[0] = initial_temperature

    cfl_number = abs(wind_speed) * DT / DX
    if cfl_number > 1.0:
        raise ValueError(f"CFL条件を満たしません: C={cfl_number:.3f}")

    for step in range(number_of_steps):
        current = predictions[step]
        if wind_speed >= 0.0:
            # 正の風では左隣が風上側になる。
            predictions[step + 1] = current - (
                wind_speed * DT / DX
            ) * (current - np.roll(current, 1))
        else:
            # 負の風では右隣が風上側になる。
            predictions[step + 1] = current - (
                wind_speed * DT / DX
            ) * (np.roll(current, -1) - current)

    return predictions


# ============================================================
# 8. 比較図
# ============================================================


def plot_results(
    x: np.ndarray,
    initial_temperature: np.ndarray,
    cnn_predictions: np.ndarray,
    upwind_predictions: np.ndarray,
    train_history: list[float],
    valid_history: list[float],
) -> None:
    """学習曲線、最終場、予測誤差を描く。"""
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.size": 11,
            "axes.labelsize": 12,
            "axes.titlesize": 12,
            "figure.dpi": 100,
            "savefig.dpi": 150,
            "savefig.bbox": "tight",
        }
    )

    final_time = N_TEST_STEPS * DT
    times = np.arange(N_TEST_STEPS + 1) * DT
    exact_all = np.asarray(
        [
            exact_advection(initial_temperature, TEST_WIND_SPEED, time)
            for time in times
        ]
    )
    cnn_rmse = np.sqrt(np.mean((cnn_predictions - exact_all) ** 2, axis=1))
    upwind_rmse = np.sqrt(
        np.mean((upwind_predictions - exact_all) ** 2, axis=1)
    )

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))

    axes[0].semilogy(train_history, label="Training", color="#2455a4")
    axes[0].semilogy(valid_history, label="Validation", color="#d1495b")
    axes[0].set_title("(a) Learning curve", loc="left", fontweight="bold")
    axes[0].set_xlabel("Epoch")
    axes[0].set_ylabel("Mean squared error")
    axes[0].legend()

    axes[1].plot(x, exact_all[-1], label="Exact", color="#222222", zorder=3)
    axes[1].plot(
        x, cnn_predictions[-1], "--", label="CNN", color="#d1495b", zorder=2
    )
    axes[1].plot(
        x,
        upwind_predictions[-1],
        ":",
        label="Upwind",
        color="#777777",
        zorder=1,
    )
    axes[1].set_title(
        f"(b) Temperature at t = {final_time:.1f} h",
        loc="left",
        fontweight="bold",
    )
    axes[1].set_xlabel("Position [km]")
    axes[1].set_ylabel("Temperature anomaly [K]")
    axes[1].legend()

    axes[2].plot(times, cnn_rmse, label="CNN", color="#d1495b")
    axes[2].plot(times, upwind_rmse, label="Upwind", color="#777777")
    axes[2].set_title("(c) Forecast error", loc="left", fontweight="bold")
    axes[2].set_xlabel("Time [h]")
    axes[2].set_ylabel("RMSE [K]")
    axes[2].legend()

    fig.tight_layout()
    output_path = FIG_DIR / "advection-cnn-comparison.png"
    fig.savefig(output_path)
    plt.close(fig)
    print(f"Saved: {output_path}")


def main() -> None:
    """データ生成、学習、反復予測、描画を順番に実行する。"""
    np.random.seed(SEED)
    torch.manual_seed(SEED)
    rng = np.random.default_rng(SEED)

    # Apple Silicon MacではMPS、それ以外ではCPUを使う。
    device = torch.device(
        "mps" if torch.backends.mps.is_available() else "cpu"
    )
    print(f"Device: {device}")

    print("Generating training data...")
    train_inputs, train_targets = generate_dataset(
        N_TRAIN_TRAJECTORIES, rng
    )
    print("Generating validation data...")
    valid_inputs, valid_targets = generate_dataset(
        N_VALID_TRAJECTORIES, rng
    )

    train_loader = make_loader(train_inputs, train_targets, shuffle=True)
    valid_loader = make_loader(valid_inputs, valid_targets, shuffle=False)

    model = AdvectionCNN().to(device)
    train_history, valid_history = train_model(
        model, train_loader, valid_loader, device
    )

    # 学習・検証データとは別の乱数から、未知の初期分布を作る。
    x = make_grid()
    test_rng = np.random.default_rng(SEED + 1)
    initial_temperature = make_random_temperature(x, test_rng)

    cnn_predictions = predict_with_cnn(
        model,
        initial_temperature,
        TEST_WIND_SPEED,
        N_TEST_STEPS,
        device,
    )
    upwind_predictions = predict_with_upwind(
        initial_temperature,
        TEST_WIND_SPEED,
        N_TEST_STEPS,
    )

    plot_results(
        x,
        initial_temperature,
        cnn_predictions,
        upwind_predictions,
        train_history,
        valid_history,
    )


if __name__ == "__main__":
    main()
