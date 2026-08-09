def prepare_signal(df, smooth_window=9, value_col="value_std"):
    df = df.copy()

    df["smooth"] = df[value_col].rolling(
        window=smooth_window,
        center=True,
        min_periods=1
    ).mean()

    baseline_value = df["smooth"].quantile(0.05)

    q95 = df["smooth"].quantile(0.95)
    q05 = df["smooth"].quantile(0.05)
    signal_range = q95 - q05

    baseline_margin = max(signal_range * 0.02, 0.3)

    df["baseline_global"] = baseline_value
    df["baseline_margin"] = baseline_margin
    df["above_baseline_global"] = df["smooth"] - baseline_value

    return df, baseline_value, baseline_margin